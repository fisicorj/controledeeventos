"""A1 (fila de e-mails) e A2 (arte do certificado otimizada)."""

import io
import shutil
import smtplib
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import User
from activities.test_utils import make_activity, make_session, make_user
from attendance.models import Attendance
from certificates.models import Certificate
from certificates.pdf import certificate_pdf, prepared_image
from registrations.models import Registration

from . import outbox
from .models import OutgoingEmail, SiteSettings

Status = OutgoingEmail.Status


class FlakyBackend:
    """Servidor de e-mail de mentira: recusa conexão enquanto `down` for verdadeiro."""

    down = True
    refused = set()

    def __init__(self, *args, **kwargs):
        pass

    def open(self):
        if FlakyBackend.down:
            raise ConnectionRefusedError('servidor fora do ar')

    def close(self):
        pass

    def send_messages(self, messages):
        for message in messages:
            if message.to[0] in FlakyBackend.refused:
                raise smtplib.SMTPRecipientsRefused({message.to[0]: (550, b'no such user')})
            mail.outbox.append(message)
        return len(messages)


class OutboxTests(TestCase):
    def queue(self, to='ana@exemplo.com', **kwargs):
        return outbox.enqueue(to, 'Assunto', 'Corpo', **kwargs)

    def test_desfeita_a_operacao_o_email_some(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.queue()
            raise RuntimeError
        self.assertFalse(OutgoingEmail.objects.exists())

    def test_envia_e_marca(self):
        with self.captureOnCommitCallbacks(execute=True):
            email = self.queue()
        email.refresh_from_db()
        self.assertEqual((email.status, email.attempts), (Status.SENT, 1))
        self.assertIsNotNone(email.sent_at)
        self.assertEqual(mail.outbox[0].to, ['ana@exemplo.com'])
        outbox.process()  # nada sai duas vezes
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND='core.test_outbox.FlakyBackend')
    def test_servidor_fora_do_ar_tenta_de_novo_sem_perder(self):
        FlakyBackend.down, FlakyBackend.refused = True, set()
        first, second = self.queue(), self.queue('bia@exemplo.com')
        with self.assertLogs('core.outbox', 'WARNING'):
            self.assertEqual(outbox.process(), (0, 1))  # parou no primeiro: o segundo nem gastou tentativa
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, first.attempts), (Status.PENDING, 1))
        self.assertEqual((second.status, second.attempts), (Status.PENDING, 0))
        self.assertGreater(first.next_try_at, timezone.now())
        self.assertIn('fora do ar', first.last_error)
        self.assertGreater(outbox.seconds_until_next(), 0)
        # passou o tempo e o servidor voltou
        OutgoingEmail.objects.update(next_try_at=timezone.now())
        FlakyBackend.down = False
        self.assertEqual(outbox.process(), (2, 0))
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['ana@exemplo.com', 'bia@exemplo.com'])
        self.assertIsNone(outbox.seconds_until_next())

    @override_settings(EMAIL_BACKEND='core.test_outbox.FlakyBackend')
    def test_desiste_depois_de_varias_tentativas_e_admin_reenvia(self):
        FlakyBackend.down, FlakyBackend.refused = True, set()
        email = self.queue()
        with self.assertLogs('core.outbox', 'WARNING'):
            for _ in range(outbox.MAX_ATTEMPTS):
                OutgoingEmail.objects.update(next_try_at=timezone.now())
                outbox.process()
        email.refresh_from_db()
        self.assertEqual((email.status, email.attempts), (Status.FAILED, outbox.MAX_ATTEMPTS))
        admin = make_user('adm', User.Role.ADMIN)
        self.client.force_login(admin)
        page = self.client.get(reverse('email_settings'))
        self.assertContains(page, 'Fila de envio')
        self.assertContains(page, 'fora do ar')
        FlakyBackend.down = False
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('outbox_retry'), follow=True)
        self.assertContains(response, '1 e-mail de volta à fila')
        email.refresh_from_db()
        self.assertEqual(email.status, Status.SENT)
        self.assertEqual(self.client.post(reverse('outbox_retry')).status_code, 302)
        self.client.force_login(make_user('org', User.Role.ORGANIZER))
        self.assertEqual(self.client.post(reverse('outbox_retry')).status_code, 403)

    @override_settings(EMAIL_BACKEND='core.test_outbox.FlakyBackend')
    def test_endereco_recusado_nao_insiste(self):
        FlakyBackend.down, FlakyBackend.refused = False, {'nao@existe.br'}
        bad, good = self.queue('nao@existe.br'), self.queue()
        with self.assertLogs('core.outbox', 'WARNING'):
            self.assertEqual(outbox.process(), (1, 1))
        bad.refresh_from_db()
        good.refresh_from_db()
        self.assertEqual((bad.status, good.status), (Status.FAILED, Status.SENT))

    def test_envio_interrompido_volta_para_a_fila(self):
        email = self.queue()
        OutgoingEmail.objects.filter(pk=email.pk).update(status=Status.SENDING, locked_at=timezone.now())
        self.assertEqual(outbox.process(), (0, 0))  # outro processo está enviando agora
        OutgoingEmail.objects.filter(pk=email.pk).update(locked_at=timezone.now() - timedelta(minutes=30))
        self.assertEqual(outbox.process(), (1, 0))  # o processo caiu: a mensagem sai

    def test_comando(self):
        self.queue()
        out = io.StringIO()
        call_command('process_outbox', stdout=out)
        self.assertIn('E-mails enviados: 1', out.getvalue())

    def test_linha_de_execucao_em_segundo_plano(self):
        with (
            override_settings(OUTBOX_MODE='thread'),
            mock.patch.object(outbox, 'process') as process,
            mock.patch.object(outbox, 'seconds_until_next', return_value=None),
            mock.patch.object(outbox.threading, 'Thread') as thread,
            mock.patch.object(outbox.connections, 'close_all'),  # o teste roda no lugar da linha de execução
        ):
            outbox.kick()
            outbox.kick()  # já rodando: não abre outra
            self.assertEqual(thread.call_count, 1)
            outbox._worker()  # roda o que a linha de execução rodaria
            self.assertTrue(process.called)
            self.assertFalse(outbox._running)
        with override_settings(OUTBOX_MODE='off'), mock.patch.object(outbox, 'process') as process:
            outbox.kick()
            process.assert_not_called()


class BulkFinishTests(TestCase):
    """Finalizar uma atividade grande só coloca os avisos na fila: a resposta não espera o envio."""

    def test_finalizar_com_muitos_inscritos(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, capacity=40)
        session = make_session(activity)
        for i in range(40):
            registration = Registration.objects.create(user=make_user(f'aluno{i}'), activity=activity)
            Attendance.objects.create(registration=registration, session=session, validated_by=org)
        self.client.force_login(org)
        with override_settings(OUTBOX_MODE='off'), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('activity_finish', args=[activity.id]), follow=True)
        self.assertContains(response, 'os avisos estão saindo por e-mail')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(OutgoingEmail.objects.filter(status=Status.PENDING).count(), 40)
        self.assertEqual(outbox.process(), (40, 0))
        self.assertEqual(len(mail.outbox), 40)


def image_file(name, size, mode='RGB', fmt='PNG'):
    buffer = io.BytesIO()
    Image.new(mode, size, (200, 180, 120) if mode == 'RGB' else (0, 0, 0, 0)).save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue())


class CertificateArtTests(TestCase):
    def setUp(self):
        self.private = tempfile.mkdtemp()
        override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.private))
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.private, True)

    def test_arte_grande_vira_jpeg_reduzido_e_reaproveitado(self):
        site = SiteSettings.load()
        site.certificate_background = image_file('fundo.png', (3508, 2480))
        site.signature_image = image_file('assinatura.png', (1600, 500), 'RGBA')
        site.signer_name = 'Diretora'
        site.save()
        path = prepared_image(site.certificate_background, 2339, opaque=True)
        self.assertTrue(path.endswith('.jpg'))
        with Image.open(path) as copy:
            self.assertEqual(copy.size, (2339, 1654))
        self.assertEqual(prepared_image(site.certificate_background, 2339, opaque=True), path)  # cache
        now = timezone.now()
        certificate = Certificate(
            code='AAAA-BBBB-CCCC', participant_name='Ana', activity_title='Curso', activity_type='Curso',
            start_date=now, end_date=now, issued_at=now,
        )  # fmt: skip
        pdf = certificate_pdf(certificate)
        self.assertIn(b'/DCTDecode', pdf)  # a arte vai como JPEG, sem recompressão
        self.assertLess(len(pdf), 150 * 1024)
        # trocou a arte: a cópia é refeita
        site.certificate_background = image_file('fundo2.png', (1200, 800))
        site.save()
        self.assertNotEqual(prepared_image(site.certificate_background, 2339, opaque=True), path)

    def test_imagem_ilegivel_nao_quebra_o_pdf(self):
        site = SiteSettings.load()
        site.certificate_background = SimpleUploadedFile('fundo.jpg', b'nao e imagem')
        site.save(update_fields=['certificate_background'])
        self.assertIsNone(prepared_image(site.certificate_background, 2339, opaque=True))
        now = timezone.now()
        certificate = Certificate(
            code='AAAA-BBBB-CCCC', participant_name='Ana', activity_title='Curso', activity_type='Curso',
            start_date=now, end_date=now, issued_at=now,
        )  # fmt: skip
        self.assertTrue(certificate_pdf(certificate).startswith(b'%PDF'))
