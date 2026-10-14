"""P3 de segurança: SEC-08 (bloqueio de login), SEC-09 (não revelar contas), SEC-11 (texto de ajuda escapado)."""

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from activities.test_utils import PASSWORD, make_user

from .models import User


class LoginLockoutTests(TestCase):
    def login(self, password, ip):
        return self.client.post(reverse('login'), {'username': 'ana', 'password': password}, REMOTE_ADDR=ip)

    def setUp(self):
        make_user('ana')

    def test_erros_de_outro_ip_nao_bloqueiam_o_dono(self):
        for _ in range(6):
            self.login('errada', '10.0.0.66')
        self.assertContains(self.login(PASSWORD, '10.0.0.66'), 'Muitas tentativas')
        response = self.login(PASSWORD, '10.0.0.5')  # o dono, de outro lugar, entra normalmente
        self.assertEqual(response.status_code, 302)

    def test_teto_por_usuario_contra_ataque_distribuido(self):
        for i in range(50):
            self.login('errada', f'10.1.{i // 250}.{i % 250}')
        self.assertContains(self.login(PASSWORD, '10.9.9.9'), 'Muitas tentativas')


class AccountEnumerationTests(TestCase):
    def test_troca_para_email_de_outra_conta_nao_revela(self):
        make_user('bia')
        ana = make_user('ana')
        self.client.force_login(ana)
        mail.outbox.clear()
        data = {'first_name': 'Ana', 'last_name': 'L', 'email': 'BIA@exemplo.com', 'institutional_id': ''}
        response = self.client.post(reverse('profile'), data, follow=True)
        self.assertContains(response, 'Enviamos um código para bia@exemplo.com')
        self.assertEqual([m.to for m in mail.outbox], [['bia@exemplo.com']])
        self.assertIn('tentativa de usar o seu e-mail', mail.outbox[0].subject)
        ana.refresh_from_db()
        self.assertEqual(ana.email, 'ana@exemplo.com')

    def test_nova_senha_vai_so_para_a_conta_confirmada(self):
        ana = make_user('ana')
        User.objects.create_user('intruso', 'ana@exemplo.com', PASSWORD, email_verified=False)
        mail.outbox.clear()
        self.client.post(reverse('password_reset'), {'email': 'ana@exemplo.com'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(ana.username, mail.outbox[0].body)
        self.assertNotIn('intruso', mail.outbox[0].body)

    def test_banco_nao_aceita_duas_contas_confirmadas_com_o_mesmo_email(self):
        make_user('ana')
        other = User(username='outra', email='ANA@exemplo.com', email_verified=True)
        with self.assertRaises(ValidationError):
            other.validate_constraints()


class HelpTextEscapeTests(TestCase):
    def test_texto_de_ajuda_e_escapado_menos_o_link_do_aviso(self):
        html = self.client.get(reverse('signup')).content.decode()
        self.assertIn(f'<a href="{reverse("privacy")}" target="_blank">Leia o aviso</a>', html)
        admin = User.objects.create_superuser('root', 'root@exemplo.com', PASSWORD)
        self.client.force_login(admin)
        html = self.client.get(reverse('site_settings')).content.decode()
        self.assertIn('&lt;nome da instituição&gt;', html)


class EmailInUseNoticeLimitTests(TestCase):
    def test_limite_de_avisos_por_ip(self):
        # SEC-16 da 2ª auditoria: um IP não manda avisos para muitas contas diferentes.
        from .verification import NOTICES_PER_IP

        for i in range(NOTICES_PER_IP + 3):
            make_user(f'dono{i}')
        mail.outbox.clear()
        for i in range(NOTICES_PER_IP + 3):
            self.client.logout()
            self.client.post(reverse('signup'), {
                'username': f'outro{i}', 'first_name': 'X', 'last_name': 'Y', 'email': f'dono{i}@exemplo.com',
                'password1': PASSWORD, 'password2': PASSWORD, 'accept_privacy': 'on',
            }, REMOTE_ADDR='10.9.9.9')  # fmt: skip
        self.assertEqual(len(mail.outbox), NOTICES_PER_IP)
