"""SEC-02: verificação em duas etapas (TOTP) e sessão curta para administradores e organizadores."""

import re
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from activities.test_utils import PASSWORD, make_user

from . import mfa
from .models import MfaDevice, User


def code_now(device, offset=0):
    return mfa.totp(mfa.secret_of(device), mfa.current_step() + offset)


class TotpTests(TestCase):
    # Vetores da RFC 6238 (SHA-1, segredo "12345678901234567890"), últimos 6 dígitos.
    SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'

    def test_vetores_da_rfc(self):
        self.assertEqual(mfa.totp(self.SECRET, 59 // 30), '287082')
        self.assertEqual(mfa.totp(self.SECRET, 1111111109 // 30), '081804')
        self.assertEqual(mfa.totp(self.SECRET, 2000000000 // 30), '279037')

    def test_janela_e_repeticao(self):
        now = 1111111109
        step = mfa.current_step(now)
        self.assertEqual(mfa.matching_step(self.SECRET, '081 804', now=now), step)
        self.assertEqual(mfa.matching_step(self.SECRET, mfa.totp(self.SECRET, step - 1), now=now), step - 1)
        self.assertIsNone(mfa.matching_step(self.SECRET, mfa.totp(self.SECRET, step - 2), now=now))
        self.assertIsNone(mfa.matching_step(self.SECRET, '081804', last_step=step, now=now))  # já usado
        self.assertIsNone(mfa.matching_step(self.SECRET, 'abcdef', now=now))

    def test_segredo_guardado_criptografado(self):
        device = mfa.pending_device(make_user('org', User.Role.ORGANIZER))
        secret = mfa.secret_of(device)
        self.assertNotIn(secret, device.secret)
        self.assertEqual(len(secret), 32)


@override_settings(MFA_REQUIRED=True)
class EnrollmentTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)

    def login(self, user):
        response = self.client.post(reverse('login'), {'username': user.username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)

    def test_cadastro_obrigatorio_no_primeiro_acesso(self):
        self.login(self.org)
        response = self.client.get(reverse('organizer_dashboard'))
        self.assertRedirects(response, reverse('mfa_setup'))
        page = self.client.get(reverse('mfa_setup'))
        self.assertContains(page, '<svg')
        self.assertContains(page, 'Ativar')
        device = MfaDevice.objects.get(user=self.org)
        self.assertIsNone(device.confirmed_at)
        wrong = self.client.post(reverse('mfa_setup'), {'code': '000000' if code_now(device) != '000000' else '111111'})
        self.assertContains(wrong, 'Código incorreto')
        response = self.client.post(reverse('mfa_setup'), {'code': code_now(device)})
        self.assertRedirects(response, reverse('mfa_codes'), fetch_redirect_response=False)
        codes_page = self.client.get(reverse('mfa_codes'))
        codes = re.findall(r'<li class="col">([a-z0-9-]+)</li>', codes_page.content.decode())
        self.assertEqual(len(codes), mfa.RECOVERY_COUNT)
        self.assertRedirects(self.client.get(reverse('mfa_codes')), reverse('mfa_setup'), target_status_code=200)
        self.assertEqual(self.client.get(reverse('organizer_dashboard')).status_code, 200)
        device.refresh_from_db()
        self.assertIsNotNone(device.confirmed_at)
        self.assertNotIn(codes[0], str(device.recovery_codes))  # só os hashes ficam no banco

    def test_participante_nao_e_afetado(self):
        ana = make_user('ana')
        self.login(ana)
        self.assertEqual(self.client.get(reverse('my_registrations')).status_code, 200)
        self.assertRedirects(self.client.get(reverse('mfa_setup')), reverse('profile'))

    @override_settings(MFA_REQUIRED=False)
    def test_opcional_quando_desligado(self):
        self.login(self.org)
        self.assertEqual(self.client.get(reverse('organizer_dashboard')).status_code, 200)
        self.assertContains(self.client.get(reverse('profile')), reverse('mfa_setup'))

    def test_sessao_curta_para_organizador(self):
        self.login(self.org)
        expiry = self.client.session.get_expiry_date()
        self.assertLess(expiry, timezone.now() + timedelta(hours=8, minutes=1))
        self.assertGreater(expiry, timezone.now() + timedelta(hours=7, minutes=59))
        self.client.logout()
        self.login(make_user('ana'))
        self.assertGreater(self.client.session.get_expiry_date(), timezone.now() + timedelta(days=13))


@override_settings(MFA_REQUIRED=True)
class LoginWithMfaTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.device = mfa.pending_device(self.org)
        self.codes = mfa.confirm(self.device, code_now(self.device, -1))  # o código atual fica livre
        self.device.refresh_from_db()

    def login(self):
        self.client.post(reverse('login'), {'username': 'org', 'password': PASSWORD})

    def test_pede_o_codigo_depois_da_senha(self):
        self.login()
        dashboard = reverse('organizer_dashboard')
        response = self.client.get(dashboard)
        self.assertRedirects(response, f'{reverse("mfa_verify")}?next={dashboard}', fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('profile')).status_code, 302)
        response = self.client.post(reverse('mfa_verify'), {'code': code_now(self.device), 'next': dashboard})
        self.assertRedirects(response, dashboard)
        self.assertEqual(self.client.get(dashboard).status_code, 200)

    def test_codigo_nao_serve_duas_vezes(self):
        code = code_now(self.device)
        self.login()
        self.client.post(reverse('mfa_verify'), {'code': code})
        self.client.logout()
        self.login()
        response = self.client.post(reverse('mfa_verify'), {'code': code})
        self.assertContains(response, 'incorreto ou já usado')

    def test_codigo_de_recuperacao_vale_uma_vez(self):
        self.login()
        response = self.client.post(reverse('mfa_verify'), {'code': self.codes[0].upper()}, follow=True)
        self.assertContains(response, f'Restam {mfa.RECOVERY_COUNT - 1}')
        self.client.logout()
        self.login()
        self.assertContains(self.client.post(reverse('mfa_verify'), {'code': self.codes[0]}), 'incorreto')

    def test_limite_de_tentativas(self):
        self.login()
        right = code_now(self.device)
        wrong = '000000' if right != '000000' else '111111'
        for _ in range(5):
            self.client.post(reverse('mfa_verify'), {'code': wrong})
        response = self.client.post(reverse('mfa_verify'), {'code': right})
        self.assertContains(response, 'Muitas tentativas')

    def test_next_externo_e_ignorado(self):
        self.login()
        response = self.client.post(
            reverse('mfa_verify'), {'code': code_now(self.device), 'next': 'https://mal.example'}
        )
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)

    def test_vale_tambem_no_admin(self):
        admin = User.objects.create_superuser('root', 'root@exemplo.com', PASSWORD)
        device = mfa.pending_device(admin)
        mfa.confirm(device, code_now(device, -1))
        self.client.post(reverse('admin:login'), {'username': 'root', 'password': PASSWORD})
        response = self.client.get(reverse('admin:index'))
        self.assertTrue(response['Location'].startswith(reverse('mfa_verify')))

    def test_desativar_exige_codigo_e_volta_ao_cadastro(self):
        self.login()
        self.client.post(reverse('mfa_verify'), {'code': code_now(self.device)})
        self.client.post(reverse('mfa_disable'), {'code': '12'})
        self.assertTrue(MfaDevice.objects.filter(user=self.org).exists())
        with mock.patch.object(mfa, 'current_step', return_value=mfa.current_step() + 1):
            self.client.post(reverse('mfa_disable'), {'code': code_now(self.device, 1)})
        self.assertFalse(MfaDevice.objects.filter(user=self.org).exists())
        self.assertRedirects(self.client.get(reverse('organizer_dashboard')), reverse('mfa_setup'))

    def test_comando_de_reset(self):
        out = StringIO()
        call_command('reset_mfa', 'ORG@exemplo.com', stdout=out)
        self.assertIn('apagada', out.getvalue())
        self.assertFalse(MfaDevice.objects.exists())
