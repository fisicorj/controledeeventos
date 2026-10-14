"""Melhorias P3 da auditoria: corridas (QUA-03), e-mail em HTML, histórico de horas, exportação da auditoria,
cadastros não confirmados e avisos de configuração (SEC-13)."""

import io
import json
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.test_utils import PASSWORD, make_activity, make_user
from certificates import audit
from certificates.models import Certificate, CertificateBatch
from core import maintenance, outbox
from core.models import SiteSettings
from registrations.models import Registration


class RaceTests(TestCase):
    def test_endereco_repetido_ao_mesmo_tempo_tenta_de_novo(self):
        org = make_user('org', User.Role.ORGANIZER)
        first = make_activity(org, title='Semana de TI')
        with mock.patch('activities.models.unique_slug', return_value=first.slug):
            second = make_activity(org, title='Semana de TI')
        self.assertNotEqual(first.slug, second.slug)
        self.assertTrue(second.slug.startswith(first.slug))

    def test_cadastro_com_usuario_tomado_no_mesmo_instante(self):
        data = {'username': 'ana', 'first_name': 'Ana', 'last_name': 'L', 'email': 'ana@exemplo.com',
                'password1': PASSWORD, 'password2': PASSWORD, 'accept_privacy': 'on'}  # fmt: skip
        with mock.patch('accounts.forms.SignUpForm.save', side_effect=IntegrityError('username')):
            response = self.client.post(reverse('signup'), data)
        self.assertContains(response, 'acabou de ser escolhido')


class HtmlEmailTests(TestCase):
    def test_mensagem_sai_com_texto_e_html_escapado(self):
        with self.captureOnCommitCallbacks(execute=True):
            outbox.enqueue('ana@exemplo.com', 'Assunto', 'Olá, <script>x</script>.\n\nVeja https://ex.com/a.\n')
        message = mail.outbox[-1]
        self.assertIn('<script>', message.body)  # o texto puro continua igual
        html, mimetype = message.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn('&lt;script&gt;x&lt;/script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('<a href="https://ex.com/a" style="color:inherit">https://ex.com/a</a>.', html)

    @override_settings(EMAIL_HTML=False)
    def test_pode_desligar(self):
        with self.captureOnCommitCallbacks(execute=True):
            outbox.enqueue('ana@exemplo.com', 'Assunto', 'Olá')
        self.assertFalse(getattr(mail.outbox[-1], 'alternatives', []))


class MyHoursTests(TestCase):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'student_email_domains': 'aluno.fsa.br'})
        self.org = make_user('org', User.Role.ORGANIZER)
        self.ana = make_user('ana', first_name='Ana')
        activity = make_activity(self.org, workload_hours=20)
        registration = Registration.objects.create(user=self.ana, activity=activity)
        moment = timezone.make_aware(timezone.datetime(2026, 3, 10, 10))
        base = {'activity_type': 'Evento', 'start_date': moment, 'end_date': moment}
        self.mine = Certificate.objects.create(registration=registration, participant_name='Ana',
                                               activity_title='Curso A', workload_hours=20,
                                               document_date=date(2026, 3, 10), **base)  # fmt: skip
        batch = CertificateBatch.objects.create(title='Semana', start_date=date(2025, 9, 1), end_date=date(2025, 9, 1),
                                                issue_date=date(2025, 9, 1), created_by=self.org)  # fmt: skip
        self.by_email = Certificate.objects.create(batch=batch, participant_name='Ana', activity_title='Semana',
                                                   participant_email='ana@exemplo.com', workload_hours=8,
                                                   document_date=date(2025, 9, 1), **base)  # fmt: skip
        self.someone_else = Certificate.objects.create(batch=batch, participant_name='Bia', activity_title='Semana',
                                                       participant_email='bia@exemplo.com', participant_document='1',
                                                       workload_hours=8, **base)  # fmt: skip
        self.client.force_login(self.ana)

    def test_horas_por_semestre(self):
        page = self.client.get(reverse('my_hours'))
        self.assertEqual([(label, hours) for label, _, hours in page.context['semesters']],
                         [('2026/1', 20), ('2025/2', 8)])  # fmt: skip
        self.assertEqual(page.context['total'], 28)
        self.assertNotContains(page, self.someone_else.code)
        csv = self.client.get(reverse('my_hours') + '?formato=csv').content.decode('utf-8-sig')
        self.assertIn('2025/2;Semana;Evento;01/09/2025;8;', csv)

    def test_declaracao_de_horas_em_pdf(self):
        response = self.client.get(reverse('my_hours') + '?formato=pdf')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('declaracao-horas.pdf', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF'))
        one = self.client.get(reverse('my_hours') + '?formato=pdf&semestre=2025/2')
        self.assertIn('declaracao-horas-2025-2.pdf', one['Content-Disposition'])
        self.assertEqual(self.client.get(reverse('my_hours') + '?formato=pdf&semestre=1999/1').status_code, 404)
        try:
            from pypdf import PdfReader
        except ImportError:  # pypdf é opcional: sem ele, confere só o arquivo
            return

        def read(pdf):
            return ' '.join(' '.join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages).split())

        text = read(response.content)
        self.assertIn('28 horas', text)
        self.assertIn(self.mine.code, text)
        self.assertIn(self.by_email.code, text)
        self.assertNotIn(self.someone_else.code, text)
        text = read(one.content)
        self.assertIn('8 horas', text)
        self.assertNotIn(self.mine.code, text)

    def test_email_nao_confirmado_ou_cpf_nao_bastam(self):
        User.objects.filter(pk=self.ana.pk).update(email_verified=False, institutional_id='1')
        page = self.client.get(reverse('my_hours'))
        self.assertEqual(page.context['count'], 1)

    def test_baixa_o_proprio_avulso_mas_nao_o_de_outra_pessoa(self):
        ok = self.client.get(reverse('certificate_download', args=[self.by_email.code]))
        self.assertEqual(ok['Content-Type'], 'application/pdf')
        other = self.client.get(reverse('certificate_download', args=[self.someone_else.code]))
        self.assertEqual(other.status_code, 404)

    def test_revogado_nao_entra(self):
        Certificate.objects.filter(pk=self.by_email.pk).update(revoked_at=timezone.now(), revoked_by=self.org)
        self.assertEqual(self.client.get(reverse('my_hours')).context['total'], 20)


class AuditExportTests(TestCase):
    def setUp(self):
        org = make_user('org', User.Role.ORGANIZER)
        moment = timezone.now()
        batch = CertificateBatch.objects.create(title='X', start_date=date(2026, 1, 1), end_date=date(2026, 1, 1),
                                                issue_date=date(2026, 1, 1), created_by=org)  # fmt: skip
        self.certificates = [
            Certificate.objects.create(
                batch=batch,
                participant_name=f'P{i}',
                activity_title='X',
                activity_type='Evento',
                start_date=moment,
                end_date=moment,
            )  # fmt: skip
            for i in range(3)
        ]
        audit.record(audit.Action.ISSUED, self.certificates[:2], actor=org)

    def test_exporta_encadeado_continua_e_detecta_alteracao(self):
        with TemporaryDirectory() as folder:
            out = StringIO()
            call_command('export_audit', pasta=folder, stdout=out)
            self.assertIn('certificados: 2 registro(s) novo(s)', out.getvalue())
            audit.record(audit.Action.REVOKED, self.certificates[2:])
            call_command('export_audit', pasta=folder, stdout=StringIO())
            files = sorted(Path(folder).glob('auditoria-certificados-*.jsonl'))
            self.assertEqual(len(files), 2)
            self.assertTrue(Path(str(files[0]) + '.sha256').exists())
            out = StringIO()
            call_command('export_audit', pasta=folder, conferir=True, stdout=out)
            self.assertIn('3 registro(s) conferido(s), cadeia íntegra', out.getvalue())
            # alguém muda um nome no primeiro arquivo
            lines = files[0].read_text(encoding='utf-8').splitlines()
            entry = json.loads(lines[0])
            entry['registro']['participant_name'] = 'Outro'
            lines[0] = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            files[0].write_text('\n'.join(lines) + '\n', encoding='utf-8')
            with self.assertRaises(CommandError):
                call_command('export_audit', pasta=folder, conferir=True, stdout=StringIO(), stderr=StringIO())

    def test_sem_pasta(self):
        with self.assertRaises(CommandError):
            call_command('export_audit', stdout=StringIO())

    def test_faxina_exporta_quando_configurada(self):
        with TemporaryDirectory() as folder, override_settings(AUDIT_EXPORT_DIR=folder):
            self.assertEqual(maintenance.export_audit(), 2)
        self.assertIn('desligada', maintenance.export_audit())

    def test_exportacao_completa_manda_ancora_por_email(self):
        from core import audit_export
        from core.models import OutgoingEmail

        with TemporaryDirectory() as folder, override_settings(AUDIT_EXPORT_EMAIL='guarda@exemplo.com'):
            results = audit_export.export_all(folder)
            self.assertEqual(results['certificados'][1], 2)
            email = OutgoingEmail.objects.get(group='audit-anchor')
            self.assertEqual(email.to, 'guarda@exemplo.com')
            last_id, last_hash = audit_export.last_state(folder, 'certificados')
            self.assertIn(f'último registro {last_id}, hash {last_hash}', email.body)
            # nada novo: não manda outra âncora
            audit_export.export_all(folder)
            self.assertEqual(OutgoingEmail.objects.filter(group='audit-anchor').count(), 1)

    def test_exportacao_simultanea_e_recusada(self):
        from django.db import connections

        from core import audit_export

        other = connections.create_connection('default')
        try:
            with other.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_lock(%s)', [audit_export.LOCK_ID])
            with TemporaryDirectory() as folder:
                with self.assertRaises(audit_export.ExportBusy):
                    audit_export.export_all(folder)
                with self.assertRaises(CommandError):
                    call_command('export_audit', pasta=folder, stdout=StringIO())
                self.assertEqual(list(Path(folder).iterdir()), [])
        finally:
            other.close()
        with TemporaryDirectory() as folder:
            self.assertEqual(audit_export.export_all(folder, notify=False)['certificados'][1], 2)


class UnverifiedAccountsTests(TestCase):
    def test_cadastros_nao_confirmados_antigos_saem(self):
        old = User.objects.create_user('velho', 'v@exemplo.com', PASSWORD, email_verified=False)
        User.objects.filter(pk=old.pk).update(date_joined=timezone.now() - timedelta(days=8))
        User.objects.create_user('novo', 'n@exemplo.com', PASSWORD, email_verified=False)
        org = User.objects.create_user('org', 'o@exemplo.com', PASSWORD, email_verified=False, role='ORGANIZER')
        User.objects.filter(pk=org.pk).update(date_joined=timezone.now() - timedelta(days=30))
        self.assertEqual(maintenance.purge_unverified_accounts(), 1)
        self.assertEqual(set(User.objects.values_list('username', flat=True)), {'novo', 'org'})

    def test_conta_excluida_pelo_participante_continua_com_o_historico(self):
        # QUA-06 da 2ª auditoria: a faxina apagava a conta anonimizada e, em cascata, inscrições e presenças.
        from accounts.privacy import anonymize
        from activities.test_utils import make_session
        from attendance.models import Attendance

        org = make_user('org', User.Role.ORGANIZER)
        past = timezone.now() - timedelta(days=20)
        activity = make_activity(org, start_date=past, end_date=past + timedelta(hours=3),
                                 registration_start=past - timedelta(days=5), registration_end=past)  # fmt: skip
        session = make_session(activity, start_at=past, end_at=past + timedelta(hours=2))
        ana = make_user('ana')
        registration = Registration.objects.create(user=ana, activity=activity)
        Attendance.objects.create(registration=registration, session=session, validated_by=org)
        anonymize(ana)
        User.objects.filter(pk=ana.pk).update(date_joined=timezone.now() - timedelta(days=400))
        self.assertEqual(maintenance.purge_unverified_accounts(), 0)
        self.assertTrue(User.objects.filter(pk=ana.pk).exists())
        self.assertEqual((Registration.objects.count(), Attendance.objects.count()), (1, 1))

    def test_apagar_conta_nao_leva_as_inscricoes(self):
        from django.db.models import ProtectedError

        ana = make_user('ana')
        Registration.objects.create(user=ana, activity=make_activity(make_user('org', User.Role.ORGANIZER)))
        with self.assertRaises(ProtectedError):
            ana.delete()


class DeployCheckTests(TestCase):
    def test_avisos(self):
        from core.checks import arena_deploy_checks

        with override_settings(SERVE_MEDIA=True, DEBUG=False, ADMIN_URL='admin/'):
            ids = {w.id for w in arena_deploy_checks(None)}
        self.assertTrue({'arena.W001', 'arena.W003'} <= ids)
        databases = {'default': {'HOST': 'db.exemplo.br', 'OPTIONS': {}}}
        with override_settings(DATABASES=databases, ADMIN_URL='gestao/'):
            ids = {w.id for w in arena_deploy_checks(None)}
        self.assertEqual(ids, {'arena.W002'})
