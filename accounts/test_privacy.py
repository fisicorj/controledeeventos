"""B2: aviso de privacidade, aceite no cadastro, cópia dos dados e exclusão da conta (LGPD)."""

import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.models import RegistrationQuestion
from activities.test_utils import PASSWORD, make_activity, make_session, make_user
from attendance.models import Attendance
from certificates.models import Certificate
from core.models import SiteSettings
from registrations.models import Registration, RegistrationAnswer


class PrivacyNoticeTests(TestCase):
    def test_pagina_publica_e_aceite_obrigatorio(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'privacy_contact_email': 'dpo@fsa.br'})
        page = self.client.get(reverse('privacy'))
        self.assertContains(page, 'Lei Geral de Proteção de Dados')
        self.assertContains(page, 'mailto:dpo@fsa.br')
        self.assertContains(self.client.get(reverse('home')), reverse('privacy'))  # link no rodapé
        data = {'username': 'ana', 'first_name': 'Ana', 'last_name': 'Lima', 'email': 'ana@gmail.com',
                'password1': PASSWORD, 'password2': PASSWORD}  # fmt: skip
        response = self.client.post(reverse('signup'), data)
        self.assertContains(response, 'é preciso concordar com o aviso de privacidade')
        self.assertFalse(User.objects.exists())
        self.client.post(reverse('signup'), {**data, 'accept_privacy': 'on'})
        self.assertIsNotNone(User.objects.get().privacy_accepted_at)


class MyDataTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, title='Oficina', capacity=1)
        self.ana = make_user('ana', first_name='Ana', institutional_id='111.444.777-35')
        self.registration = Registration.objects.create(user=self.ana, activity=self.activity)
        question = RegistrationQuestion.objects.create(activity=self.activity, label='Curso')
        RegistrationAnswer.objects.create(registration=self.registration, question=question, value='ADS')
        self.client.force_login(self.ana)

    def test_copia_dos_dados(self):
        self.assertContains(self.client.get(reverse('profile')), reverse('my_data'))
        response = self.client.get(reverse('my_data'))
        self.assertIn('attachment', response['Content-Disposition'])
        data = json.loads(response.content)
        self.assertEqual(data['conta']['email'], 'ana@exemplo.com')
        self.assertEqual(data['conta']['matricula_ou_cpf'], '111.444.777-35')
        self.assertEqual(data['inscricoes'][0]['atividade'], 'Oficina')
        self.assertEqual(data['inscricoes'][0]['respostas'], {'Curso': 'ADS'})

    def test_excluir_conta(self):
        bia = make_user('bia')
        waiting = Registration.objects.create(
            user=bia, activity=self.activity, status='WAITLISTED', waitlisted_at=timezone.now()
        )
        # atividade passada com certificado: o certificado continua valendo
        past = make_activity(self.org, title='Palestra antiga', start_date=timezone.now() - timedelta(days=10),
                             end_date=timezone.now() - timedelta(days=9),
                             registration_start=timezone.now() - timedelta(days=20),
                             registration_end=timezone.now() - timedelta(days=10), status='FINISHED')  # fmt: skip
        old = Registration.objects.create(user=self.ana, activity=past)
        Attendance.objects.create(registration=old, session=make_session(past), validated_by=self.org)
        certificate = Certificate.objects.create(
            registration=old, participant_name='Ana', activity_title='Palestra antiga', activity_type='Evento',
            start_date=past.start_date, end_date=past.end_date,
        )  # fmt: skip
        url = reverse('delete_account')
        self.assertContains(self.client.get(url), '1 inscrição em atividade que ainda não começou será cancelada')
        response = self.client.post(url, {'password': 'errada', 'confirm': 'EXCLUIR'})
        self.assertContains(response, 'Senha incorreta')
        response = self.client.post(url, {'password': PASSWORD, 'confirm': 'excluir'}, follow=True)
        self.assertContains(response, 'Sua conta foi excluída')
        self.ana.refresh_from_db()
        self.assertEqual((self.ana.first_name, self.ana.institutional_id, self.ana.is_active), ('Conta', '', False))
        self.assertTrue(self.ana.email.endswith('.invalid'))
        self.assertFalse(self.ana.has_usable_password())
        self.assertFalse(RegistrationAnswer.objects.exists())
        self.registration.refresh_from_db()
        waiting.refresh_from_db()
        self.assertEqual((self.registration.status, waiting.status), ('CANCELLED', 'CONFIRMED'))  # vaga liberada
        self.assertTrue(Attendance.objects.filter(registration=old).exists())
        self.assertContains(self.client.get(reverse('certificate_verify_code', args=[certificate.code])), 'Ana')
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertFalse(self.client.login(username='ana', password=PASSWORD))

    def test_organizador_nao_exclui_por_aqui(self):
        self.client.force_login(self.org)
        self.assertNotContains(self.client.get(reverse('profile')), reverse('delete_account'))
        response = self.client.post(
            reverse('delete_account'), {'password': PASSWORD, 'confirm': 'EXCLUIR'}, follow=True
        )
        self.assertContains(response, 'não podem ser excluídas por aqui')
        self.assertTrue(User.objects.get(pk=self.org.pk).is_active)
