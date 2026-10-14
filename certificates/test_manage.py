"""A4 (revogar e corrigir certificado de atividade) e A5 (desfazer presença)."""

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from activities.test_utils import make_activity, make_session, make_user
from attendance.models import Attendance
from registrations.models import Registration

from .models import Certificate, CertificateEvent


class ActivityCertificateTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, min_attendance_percent=50)
        self.sessions = [make_session(self.activity, title=f'Aula {i}') for i in (1, 2)]
        self.ana = make_user('ana', first_name='Ana', last_name='Sousa', institutional_id='123')
        self.registration = Registration.objects.create(user=self.ana, activity=self.activity)
        for session in self.sessions:
            Attendance.objects.create(registration=self.registration, session=session, validated_by=self.org)
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('activity_finish', args=[self.activity.id]))
        self.certificate = Certificate.objects.get()

    def test_corrigir_nome_com_auditoria(self):
        manage = self.client.get(reverse('activity_manage', args=[self.activity.id]))
        self.assertContains(manage, reverse('certificate_correct', args=[self.certificate.code]))
        # a participante corrigiu o nome no cadastro; o formulário já vem com o nome novo
        User.objects.filter(pk=self.ana.pk).update(last_name='Souza')
        url = reverse('certificate_correct', args=[self.certificate.code])
        self.assertContains(self.client.get(url), 'value="Ana Souza"')
        response = self.client.post(
            url,
            {'participant_name': 'Ana  Souza', 'participant_document': '123', 'reason': 'Sobrenome digitado errado'},
            follow=True,
        )
        self.assertContains(response, 'corrigido')
        self.certificate.refresh_from_db()
        self.assertEqual(self.certificate.participant_name, 'Ana Souza')
        event = CertificateEvent.objects.get(action=CertificateEvent.Action.UPDATED)
        self.assertEqual(event.details['changes'], {'participant_name': ['Ana Sousa', 'Ana Souza']})
        self.assertIn('Nome: Ana Sousa → Ana Souza', event.summary)
        self.assertIn('Sobrenome digitado errado', event.summary)
        # sem mudança, nada é registrado
        self.client.post(url, {'participant_name': 'Ana Souza', 'participant_document': '123', 'reason': 'De novo'})
        self.assertEqual(CertificateEvent.objects.filter(action=CertificateEvent.Action.UPDATED).count(), 1)

    def test_revogar(self):
        url = reverse('certificate_revoke', args=[self.certificate.code])
        self.assertContains(self.client.get(url), 'não é emitido de novo automaticamente')
        self.client.post(url, {'reason': 'Presença lançada por engano'})
        self.certificate.refresh_from_db()
        self.assertTrue(self.certificate.is_revoked)
        self.assertContains(self.client.get(reverse('activity_manage', args=[self.activity.id])), 'Revogado')
        self.assertContains(
            self.client.get(reverse('certificate_verify_code', args=[self.certificate.code])), 'revogado'
        )
        # pendentes não reemitem o revogado; correção também não vale mais
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('certificates_issue', args=[self.activity.id]))
        self.assertEqual(Certificate.objects.count(), 1)
        response = self.client.get(reverse('certificate_correct', args=[self.certificate.code]), follow=True)
        self.assertContains(response, 'revogado não pode ser corrigido')
        # a participante vê que foi revogado e não baixa mais
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(reverse('my_registrations')), 'foi revogado pela organização')
        response = self.client.get(reverse('certificate_download', args=[self.certificate.code]), follow=True)
        self.assertContains(response, 'revogado e não pode mais ser baixado')

    def test_permissao(self):
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        for name in ('certificate_revoke', 'certificate_correct'):
            self.assertEqual(self.client.get(reverse(name, args=[self.certificate.code])).status_code, 403)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('certificate_revoke', args=[self.certificate.code])).status_code, 403)


class RemoveAttendanceTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, min_attendance_percent=100)
        self.session = make_session(self.activity)
        self.ana = make_user('ana', first_name='Ana')
        self.registration = Registration.objects.create(user=self.ana, activity=self.activity)
        Attendance.objects.create(registration=self.registration, session=self.session, validated_by=self.org)
        self.url = reverse('remove_checkin', args=[self.session.id, self.registration.id])
        self.client.force_login(self.org)

    def test_desfazer_presenca(self):
        page = self.client.get(reverse('session_attendance', args=[self.session.id]))
        self.assertContains(page, 'Desfazer')
        self.assertEqual(self.client.get(self.url).status_code, 405)
        response = self.client.post(self.url, follow=True)
        self.assertContains(response, 'Presença de Ana removida')
        self.assertFalse(Attendance.objects.exists())
        response = self.client.post(self.url, follow=True)
        self.assertContains(response, 'já não estava registrada')

    def test_avisa_quando_ja_tem_certificado(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('activity_finish', args=[self.activity.id]))
        certificate = Certificate.objects.get()
        response = self.client.post(self.url, follow=True)  # finalizada: ainda dá para corrigir a presença
        self.assertContains(response, f'já tem certificado ({certificate.code})')
        self.assertContains(response, 'revogue-o')

    def test_permissao(self):
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertTrue(Attendance.objects.exists())


class BatchCorrectionTests(TestCase):
    def test_corrigir_certificado_avulso(self):
        from datetime import date

        from django.utils import timezone

        from .models import CertificateBatch

        org = make_user('org', User.Role.ORGANIZER)
        batch = CertificateBatch.objects.create(
            title='Palestra', start_date=date(2026, 9, 1), end_date=date(2026, 9, 1), created_by=org
        )
        base = {'batch': batch, 'activity_title': 'Palestra', 'activity_type': 'Evento',
                'start_date': timezone.now(), 'end_date': timezone.now()}  # fmt: skip
        ana = Certificate.objects.create(participant_name='Ana Sousa', participant_document='1', **base)
        Certificate.objects.create(participant_name='Bia', participant_document='2', **base)
        self.client.force_login(org)
        url = reverse('certificate_correct', args=[ana.code])
        self.assertContains(self.client.get(reverse('batch_detail', args=[batch.pk])), url)
        self.assertContains(self.client.get(url), 'value="Ana Sousa"')  # avulso: vem com os dados do certificado
        response = self.client.post(url, {'participant_name': 'Ana', 'participant_document': '2', 'reason': 'Teste'})
        self.assertContains(response, 'já usa esta matrícula')
        response = self.client.post(
            url, {'participant_name': 'Ana Souza', 'participant_document': '1', 'reason': 'Nome errado'}
        )
        self.assertRedirects(response, reverse('batch_detail', args=[batch.pk]))
        ana.refresh_from_db()
        self.assertEqual(ana.participant_name, 'Ana Souza')
        batch.cancelled_at, batch.cancelled_by = timezone.now(), org
        batch.save()
        response = self.client.get(url, follow=True)
        self.assertContains(response, 'foi cancelada e não pode mais ser alterada')


class PublicDocumentAndDoubleClickTests(TestCase):
    def test_cpf_mascarado_na_validacao_publica(self):
        from datetime import date

        from django.utils import timezone

        from .models import CertificateBatch

        org = make_user('org', User.Role.ORGANIZER)
        batch = CertificateBatch.objects.create(
            title='Palestra', start_date=date(2026, 9, 1), end_date=date(2026, 9, 1), created_by=org
        )
        base = {'batch': batch, 'activity_title': 'Palestra', 'activity_type': 'Evento',
                'start_date': timezone.now(), 'end_date': timezone.now()}  # fmt: skip
        cpf = Certificate.objects.create(participant_name='Ana', participant_document='111.444.777-35', **base)
        ra = Certificate.objects.create(participant_name='Bia', participant_document='12546456', **base)
        page = self.client.get(reverse('certificate_verify_code', args=[cpf.code]))
        self.assertContains(page, '***.444.777-**')
        self.assertNotContains(page, '111.444.777-35')
        self.assertContains(self.client.get(reverse('certificate_verify_code', args=[ra.code])), '12546456')

    def test_pesquisa_respondida_duas_vezes_ao_mesmo_tempo(self):
        from unittest import mock

        from django.db import IntegrityError

        from activities.models import Activity
        from registrations.models import SurveyResponse

        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, status=Activity.Status.FINISHED)
        ana = make_user('ana')
        registration = Registration.objects.create(user=ana, activity=activity)
        self.client.force_login(ana)
        # simula a segunda requisição chegando depois de a primeira gravar
        with mock.patch.object(SurveyResponse, 'save', side_effect=IntegrityError):
            response = self.client.post(reverse('survey', args=[registration.pk]), {'score': 9}, follow=True)
        self.assertContains(response, 'Você já respondeu a pesquisa')
