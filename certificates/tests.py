from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from activities.models import Activity
from activities.test_utils import make_activity, make_session, make_user, now
from attendance.models import Attendance
from registrations.models import Registration

from .models import Certificate
from .views import normalize_code


class CertificateFlowTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER, first_name='Manoel', last_name='Prof')
        self.activity = make_activity(self.org, title='Curso de Django', workload_hours=8, min_attendance_percent=75)
        self.sessions = [
            make_session(self.activity, title=f'Aula {i}', start_at=self.activity.start_date) for i in range(1, 5)
        ]
        self.ana = make_user('ana', first_name='Ana', last_name='Souza')
        self.bia = make_user('bia', first_name='Bia', last_name='Lima')
        self.reg_ana = Registration.objects.create(user=self.ana, activity=self.activity)
        self.reg_bia = Registration.objects.create(user=self.bia, activity=self.activity)
        for session in self.sessions[:3]:  # Ana: 3/4 = 75%  (apta)
            Attendance.objects.create(registration=self.reg_ana, session=session, validated_by=self.org)
        for session in self.sessions[:2]:  # Bia: 2/4 = 50% (não apta)
            Attendance.objects.create(registration=self.reg_bia, session=session, validated_by=self.org)
        self.client.force_login(self.org)

    def finish(self):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse('activity_finish', args=[self.activity.id]), follow=True)

    def test_finalizar_emite_so_para_quem_atingiu_a_frequencia(self):
        preview = self.client.get(reverse('activity_finish', args=[self.activity.id]))
        self.assertContains(preview, '<strong>1 de 2</strong>')
        response = self.finish()
        self.assertContains(response, '1 certificado(s) emitido(s)')
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.status, Activity.Status.FINISHED)
        self.assertFalse(self.activity.sessions.filter(check_in_open=True).exists())
        certificate = Certificate.objects.get()
        self.assertEqual(certificate.registration, self.reg_ana)
        self.assertEqual(certificate.participant_name, 'Ana Souza')
        self.assertEqual(certificate.attendance_percent, 75)
        self.assertEqual([m.to[0] for m in mail.outbox], ['ana@exemplo.com'])
        self.assertIn(certificate.code, mail.outbox[0].body)

    def test_emitir_pendentes_e_idempotente(self):
        self.finish()
        Attendance.objects.create(registration=self.reg_bia, session=self.sessions[2], validated_by=self.org)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('certificates_issue', args=[self.activity.id]))
            self.client.post(reverse('certificates_issue', args=[self.activity.id]))
        self.assertEqual(Certificate.objects.count(), 2)

    def test_certificados_desligados(self):
        Activity.objects.filter(pk=self.activity.pk).update(certificate_enabled=False)
        self.finish()
        self.assertFalse(Certificate.objects.exists())

    def test_download_e_validacao(self):
        self.finish()
        certificate = Certificate.objects.get()
        url = reverse('certificate_download', args=[certificate.code])

        self.client.force_login(self.ana)
        page = self.client.get(reverse('my_registrations'))
        self.assertContains(page, url)
        response = self.client.get(url)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))

        self.client.force_login(self.bia)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertContains(self.client.get(reverse('my_registrations')), 'exige pelo menos 75%')

        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)  # precisa de login para baixar
        verify = self.client.get(reverse('certificate_verify'), {'codigo': certificate.code.lower().replace('-', ' ')})
        self.assertRedirects(verify, reverse('certificate_verify_code', args=[certificate.code]))
        page = self.client.get(reverse('certificate_verify_code', args=[certificate.code]))
        self.assertContains(page, 'Certificado autêntico')
        self.assertContains(page, 'Ana Souza')
        self.assertContains(page, '8 horas')
        self.assertContains(self.client.get(reverse('certificate_verify_code', args=['AAAA-BBBB-CCCC'])), 'Nenhum')

    def test_editar_status_para_finalizado_tambem_emite(self):
        a = self.activity
        fmt = '%Y-%m-%dT%H:%M'
        from django.utils import timezone

        def lt(d):
            return timezone.localtime(d).strftime(fmt)

        data = {
            'title': a.title, 'description': a.description, 'activity_type': a.activity_type,
            'start_date': lt(a.start_date), 'end_date': lt(a.end_date),
            'registration_start': lt(a.registration_start), 'registration_end': lt(a.registration_end),
            'capacity': a.capacity, 'location': '', 'status': 'FINISHED', 'certificate_enabled': 'on',
            'workload_hours': 8, 'min_attendance_percent': 75,
        }  # fmt: skip
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('activity_edit', args=[a.id]), data)
        self.assertEqual(Certificate.objects.count(), 1)


class CodeTests(TestCase):
    def test_normaliza_codigo(self):
        self.assertEqual(normalize_code('abcd efgh jkmn'), 'ABCD-EFGH-JKMN')
        self.assertIsNone(normalize_code('curto'))

    def test_codigo_sem_caracteres_ambiguos(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org)
        codes = set()
        for i in range(30):
            reg = Registration.objects.create(user=make_user(f'u{i}'), activity=activity)
            cert = Certificate.objects.create(
                registration=reg, participant_name='X', activity_title='Y', activity_type='Curso',
                start_date=now(), end_date=now(), attendance_percent=100,
            )  # fmt: skip
            codes.add(cert.code)
            self.assertRegex(cert.code, r'^[A-HJ-KM-NP-Z2-9]{4}-[A-HJ-KM-NP-Z2-9]{4}-[A-HJ-KM-NP-Z2-9]{4}$')
        self.assertEqual(len(codes), 30)
