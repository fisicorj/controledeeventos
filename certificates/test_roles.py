"""Certificados de palestrante e de apresentação de trabalho."""

import tempfile
from datetime import timedelta
from pathlib import Path

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.models import Activity, Speaker
from activities.test_utils import make_activity, make_session, make_user
from core.models import SiteSettings
from submissions.models import CallForPapers, Submission

from .models import Certificate
from .pdf import certificate_text
from .services import finish_activity

Role = Certificate.Role


def flat(certificate):
    body = certificate_text(certificate, SiteSettings.load())['body']
    return ' '.join(''.join(text for text, _ in line) for line in body)


class RoleSetup(TestCase):
    def setUp(self):
        self.private = tempfile.mkdtemp()
        override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.private))
        override.enable()
        self.addCleanup(override.disable)
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, title='Semana de TI', activity_type=Activity.Type.EVENT, capacity=10)
        start = self.activity.start_date
        self.s1 = make_session(self.activity, title='Abertura', start_at=start, end_at=start + timedelta(hours=1))
        self.s2 = make_session(
            self.activity,
            title='Redes e segurança',
            start_at=start + timedelta(hours=2),
            end_at=start + timedelta(hours=4),
        )
        self.speaker = Speaker.objects.create(
            activity=self.activity, name='Paula Palestrante', email='paula@exemplo.com', document='123'
        )
        self.speaker.sessions.add(self.s1, self.s2)
        self.mute = Speaker.objects.create(activity=self.activity, name='Sem Email')

    def finish(self):
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            return finish_activity(self.activity)

    def add_work(self, status=Submission.Status.ACCEPTED, session=None):
        call = CallForPapers.objects.get_or_create(
            activity=self.activity,
            defaults={
                'opens_at': timezone.now() - timedelta(days=2),
                'closes_at': timezone.now() + timedelta(hours=1),
                'instructions': 'x',
            },
        )[0]
        self.author = make_user('autora', first_name='Ana', last_name='Autora')
        return Submission.objects.create(
            call=call,
            author=self.author,
            title='Ensino de redes',
            abstract='Resumo',
            coauthors='Bruno Coautor (Fatec)',
            file=SimpleUploadedFile('t.pdf', b'%PDF-1.4'),
            status=status,
            session=session,
        )


class SpeakerTests(RoleSetup):
    def test_palestrante_recebe_certificado_com_sessoes_e_horas(self):
        issued = self.finish()
        certificate = Certificate.objects.get(speaker=self.speaker)
        self.assertIn(certificate, issued)
        self.assertEqual(certificate.role, Role.SPEAKER)
        self.assertEqual(certificate.workload_hours, 3)
        self.assertEqual(certificate.role_detail, '“Abertura” e “Redes e segurança”')
        self.assertEqual(certificate.participant_document, '123')
        text = flat(certificate)
        self.assertIn('atuou como palestrante em “Semana de TI”', text)
        self.assertIn('nas sessões “Abertura” e “Redes e segurança”', text)
        # E-mail com o PDF anexo só para quem tem endereço
        to_paula = [m for m in mail.outbox if m.to == ['paula@exemplo.com']]
        self.assertEqual(len(to_paula), 1)
        self.assertEqual(to_paula[0].attachments[0][2], 'application/pdf')
        self.assertTrue(Certificate.objects.filter(speaker=self.mute).exists())
        # Sem sessões: período e carga da atividade
        self.assertEqual(Certificate.objects.get(speaker=self.mute).workload_hours, self.activity.workload_hours)

    def test_conta_com_o_mesmo_email_ve_nas_horas(self):
        self.finish()
        code = Certificate.objects.get(speaker=self.speaker).code
        paula = User.objects.create_user('paula', 'paula@exemplo.com', 'Senha!forte123', email_verified=True)
        self.client.force_login(paula)
        self.assertContains(self.client.get(reverse('my_hours')), 'Palestrante: Semana de TI')
        self.assertEqual(
            self.client.get(reverse('certificate_download', args=[code]))['Content-Type'], 'application/pdf'
        )
        self.client.force_login(make_user('outro'))
        self.assertEqual(self.client.get(reverse('certificate_download', args=[code])).status_code, 404)

    def test_validacao_publica_mostra_o_papel(self):
        self.finish()
        certificate = Certificate.objects.get(speaker=self.speaker)
        page = self.client.get(reverse('certificate_verify_code', args=[certificate.code]))
        self.assertContains(page, 'Palestrante')
        self.assertContains(page, 'Redes e segurança')

    def test_opcao_desligada_nao_emite(self):
        Activity.objects.filter(pk=self.activity.pk).update(speaker_certificate=False)
        self.activity.refresh_from_db()
        self.finish()
        self.assertFalse(Certificate.objects.filter(speaker__isnull=False).exists())

    def test_palestrante_incluido_depois_sai_nos_pendentes(self):
        self.finish()
        late = Speaker.objects.create(activity=self.activity, name='Atrasado')
        self.client.force_login(self.org)
        self.client.post(reverse('certificates_issue', args=[self.activity.pk]))
        self.assertTrue(Certificate.objects.filter(speaker=late).exists())
        self.assertEqual(Certificate.objects.filter(speaker=self.speaker).count(), 1)

    def test_gestao_lista_e_palestrante_com_certificado_nao_e_removido(self):
        self.finish()
        self.client.force_login(self.org)
        page = self.client.get(reverse('activity_manage', args=[self.activity.pk]))
        self.assertContains(page, 'Palestrantes e apresentações')
        self.assertContains(
            page, reverse('certificate_revoke', args=[Certificate.objects.get(speaker=self.speaker).code])
        )
        response = self.client.post(reverse('speaker_delete', args=[self.speaker.pk]), follow=True)
        self.assertContains(response, 'já tem certificado de palestrante')
        self.assertTrue(Speaker.objects.filter(pk=self.speaker.pk).exists())


class PresenterTests(RoleSetup):
    def test_autor_do_trabalho_aprovado_recebe_certificado(self):
        work = self.add_work(session=self.s2)
        rejected = self.add_work_rejected()
        self.finish()
        certificate = Certificate.objects.get(submission=work)
        self.assertEqual(certificate.role, Role.PRESENTER)
        self.assertEqual(certificate.participant_name, 'Ana Autora')
        self.assertEqual(certificate.role_detail, 'Ana Autora e Bruno Coautor (Fatec)')
        self.assertEqual(certificate.workload_hours, 2)  # horas da sessão em que apresentou
        self.assertFalse(Certificate.objects.filter(submission=rejected).exists())
        text = flat(certificate)
        self.assertIn('apresentou o trabalho “Ensino de redes”', text)
        self.assertIn('de autoria de Ana Autora e Bruno Coautor (Fatec)', text)
        self.assertIn('no evento “Semana de TI”', text)
        email = next(m for m in mail.outbox if m.to == ['autora@exemplo.com'])
        self.assertIn('apresentação de trabalho', email.subject)

        self.client.force_login(self.author)
        self.assertContains(self.client.get(reverse('my_hours')), 'Apresentação: Ensino de redes (Semana de TI)')
        response = self.client.get(reverse('certificate_download', args=[certificate.code]))
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def add_work_rejected(self):
        call = CallForPapers.objects.get(activity=self.activity)
        return Submission.objects.create(
            call=call,
            author=make_user('rejeitado'),
            title='Outro',
            abstract='x',
            file=SimpleUploadedFile('t.pdf', b'%PDF-1.4'),
            status=Submission.Status.REJECTED,
        )

    def test_opcao_desligada_nao_emite(self):
        Activity.objects.filter(pk=self.activity.pk).update(presenter_certificate=False)
        self.activity.refresh_from_db()
        self.add_work()
        self.finish()
        self.assertFalse(Certificate.objects.filter(submission__isnull=False).exists())

    def test_revogar_volta_para_a_gestao(self):
        from accounts.reauth import SESSION_KEY

        work = self.add_work()
        self.finish()
        certificate = Certificate.objects.get(submission=work)
        self.client.force_login(self.org)
        session = self.client.session
        session[SESSION_KEY] = timezone.now().timestamp()
        session.save()
        response = self.client.post(
            reverse('certificate_revoke', args=[certificate.code]), {'reason': 'Não apresentou'}
        )
        self.assertRedirects(response, reverse('activity_manage', args=[self.activity.pk]))
        certificate.refresh_from_db()
        self.assertTrue(certificate.is_revoked)


class PresenterAttendanceTests(RoleSetup):
    def test_exigir_presenca_do_autor(self):
        from attendance.models import Attendance
        from registrations.models import Registration

        Activity.objects.filter(pk=self.activity.pk).update(presenter_requires_attendance=True)
        self.activity.refresh_from_db()
        work = self.add_work(session=self.s2)
        self.finish()
        self.assertFalse(Certificate.objects.filter(submission=work).exists())
        registration = Registration.objects.create(user=self.author, activity=self.activity)
        Attendance.objects.create(registration=registration, session=self.s1, validated_by=self.org)
        self.client.force_login(self.org)
        self.client.post(reverse('certificates_issue', args=[self.activity.pk]))
        self.assertFalse(Certificate.objects.filter(submission=work).exists())  # outra sessão não vale
        Attendance.objects.create(registration=registration, session=self.s2, validated_by=self.org)
        self.client.post(reverse('certificates_issue', args=[self.activity.pk]))
        self.assertTrue(Certificate.objects.filter(submission=work).exists())


class CoauthorTests(RoleSetup):
    """APP-09: coautor com e-mail informado recebe o próprio certificado de apresentação."""

    def setUp(self):
        super().setUp()
        self.work = self.add_work(session=self.s2)
        Submission.objects.filter(pk=self.work.pk).update(
            coauthors='Bruno Coautor (Fatec) <bruno@exemplo.com>\nCarla Sem Email (USP)'
        )
        self.work.refresh_from_db()

    def test_coautor_com_email_recebe_o_seu(self):
        self.finish()
        certificates = Certificate.objects.filter(submission=self.work).order_by('coauthor_email')
        self.assertEqual([c.participant_name for c in certificates], ['Ana Autora', 'Bruno Coautor (Fatec)'])
        bruno = certificates[1]
        self.assertEqual(bruno.coauthor_email, 'bruno@exemplo.com')
        self.assertEqual(bruno.role_detail, 'Ana Autora, Bruno Coautor (Fatec) e Carla Sem Email (USP)')
        self.assertTrue(any(m.to == ['bruno@exemplo.com'] for m in mail.outbox))
        self.assertNotIn('bruno@exemplo.com', flat(bruno))

        # Emitir de novo não duplica; coautor incluído depois sai nos pendentes.
        Submission.objects.filter(pk=self.work.pk).update(coauthors=self.work.coauthors + '\nDiego <diego@exemplo.com>')
        self.client.force_login(self.org)
        self.client.post(reverse('certificates_issue', args=[self.activity.pk]))
        self.assertEqual(Certificate.objects.filter(submission=self.work).count(), 3)

    def test_minhas_horas_do_coautor_e_nao_do_autor(self):
        self.finish()
        bruno = make_user('bruno')
        User.objects.filter(pk=bruno.pk).update(email_verified=True)
        self.client.force_login(bruno)
        page = self.client.get(reverse('my_hours'))
        self.assertContains(page, 'Apresentação: Ensino de redes')
        code = Certificate.objects.get(coauthor_email='bruno@exemplo.com').code
        self.assertEqual(self.client.get(reverse('certificate_download', args=[code])).status_code, 200)
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(reverse('certificate_download', args=[code])).status_code, 404)

    def test_presenca_exigida_vale_para_o_coautor(self):
        from attendance.models import Attendance
        from registrations.models import Registration

        Activity.objects.filter(pk=self.activity.pk).update(presenter_requires_attendance=True)
        self.activity.refresh_from_db()
        bruno = make_user('bruno')
        User.objects.filter(pk=bruno.pk).update(email_verified=True)
        registration = Registration.objects.create(user=bruno, activity=self.activity)
        Attendance.objects.create(registration=registration, session=self.s2, validated_by=self.org)
        self.finish()
        self.assertEqual(
            list(Certificate.objects.filter(submission=self.work).values_list('coauthor_email', flat=True)),
            ['bruno@exemplo.com'],  # a autora não teve presença
        )


class CoauthorFormTests(TestCase):
    def setUp(self):
        activity = make_activity(make_user('org', User.Role.ORGANIZER))
        self.call = CallForPapers.objects.create(
            activity=activity, opens_at=timezone.now(), closes_at=activity.end_date, max_authors=5
        )

    def form(self, coauthors):
        from submissions.forms import SubmissionForm

        call = self.call
        form = SubmissionForm(
            {'title': 'T', 'abstract': 'R', 'coauthors': coauthors},
            {'file': SimpleUploadedFile('t.pdf', b'%PDF-1.4')},
            call=call,
            author_email='eu@exemplo.com',
        )
        form.is_valid()
        return form.errors.get('coauthors')

    def test_validacao(self):
        self.assertIsNone(self.form('Bruno (Fatec) bruno@exemplo.com\nCarla (USP)'))
        self.assertIn('e-mail só', self.form('Bruno bruno@a.com outro@b.com')[0])
        self.assertIn('próprio e-mail', self.form('Eu mesmo eu@exemplo.com')[0])
        self.assertIn('mais de um coautor', self.form('A a@x.com\nB a@x.com')[0])
        self.assertIn('Falta o nome', self.form('a@x.com')[0])
