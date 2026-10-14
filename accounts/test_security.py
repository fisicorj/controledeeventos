"""A3 (limite de tentativas no /admin/) e A6 (conta não confirmada não segura o e-mail)."""

import re

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from activities.test_utils import PASSWORD, make_activity, make_user
from core.models import SiteSettings, Throttle

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


class AdminLoginThrottleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('chefe', 'chefe@exemplo.com', PASSWORD)
        self.url = reverse('admin:login')

    def test_forca_bruta_no_admin_e_bloqueada(self):
        for _ in range(5):
            self.client.post(self.url, {'username': 'chefe', 'password': 'errada'})
        response = self.client.post(self.url, {'username': 'chefe', 'password': PASSWORD})
        self.assertContains(response, 'Muitas tentativas de entrar')
        self.assertNotIn('_auth_user_id', self.client.session)
        Throttle.objects.all().delete()
        response = self.client.post(self.url, {'username': 'chefe', 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)

    def test_contador_e_o_mesmo_do_portal(self):
        for _ in range(3):
            self.client.post(reverse('login'), {'username': 'chefe', 'password': 'errada'})
        for _ in range(2):
            self.client.post(self.url, {'username': 'chefe', 'password': 'errada'})
        response = self.client.post(reverse('login'), {'username': 'chefe', 'password': PASSWORD})
        self.assertContains(response, 'Muitas tentativas de entrar')


def last_code():
    return re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)


@override_settings(EMAIL_BACKEND=LOCMEM)
class EmailSquattingTests(TestCase):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'student_email_domains': 'aluno.fsa.br'})

    def signup(self, username, email='12546456@aluno.fsa.br', document='12546456'):
        return self.client.post(
            reverse('signup'),
            {'username': username, 'first_name': 'Joana', 'last_name': 'Rego', 'email': email,
             'institutional_id': document, 'password1': PASSWORD, 'password2': PASSWORD, 'accept_privacy': 'on'},
        )  # fmt: skip

    def test_conta_nao_confirmada_nao_trava_o_dono_do_email(self):
        self.signup('intruso')  # alguém cadastra o e-mail institucional de outra pessoa e nunca confirma
        self.assertFalse(User.objects.get(username='intruso').email_verified)
        self.client.logout()
        response = self.signup('joana')  # a dona do e-mail consegue se cadastrar
        self.assertRedirects(response, reverse('verify_email'))
        self.assertFalse(User.objects.filter(username='intruso').exists())
        self.client.post(reverse('verify_email'), {'code': last_code()})
        self.assertTrue(User.objects.get(username='joana').student_id_confirmed)

    def test_conta_confirmada_continua_dona_do_email(self):
        ana = make_user('ana')
        mail.outbox.clear()
        response = self.signup('outra', email='ana@exemplo.com', document='')
        # A resposta é a de sempre (SEC-09): não revela que o e-mail já tem conta.
        self.assertRedirects(response, reverse('verify_email'))
        self.assertNotContains(self.client.get(reverse('verify_email')), 'Já existe')
        # Nenhum código sai: a dona do e-mail recebe só um aviso, com o usuário dela e o link de nova senha.
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('tentativa de usar o seu e-mail', mail.outbox[0].subject)
        self.assertIn('usuário: ana', mail.outbox[0].body)
        self.assertNotRegex(mail.outbox[0].body, r'\b\d{6}\b')
        response = self.client.post(reverse('verify_email'), {'code': '123456'})
        self.assertContains(response, 'incorreto')
        outra = User.objects.get(username='outra')
        self.assertFalse(outra.email_verified)
        ana.refresh_from_db()
        self.assertTrue(ana.email_verified)

    def test_aviso_ao_dono_tem_limite(self):
        make_user('ana')
        mail.outbox.clear()
        for i in range(5):
            self.client.logout()
            self.signup(f'outra{i}', email='ana@exemplo.com', document='')
        self.assertEqual(len(mail.outbox), 3)

    def test_conta_nao_confirmada_com_historico_nao_e_apagada(self):
        self.signup('intruso')
        intruso = User.objects.get(username='intruso')
        intruso.role = User.Role.ORGANIZER
        intruso.save()
        make_activity(intruso)  # já tem atividade ligada à conta
        self.client.logout()
        response = self.signup('joana')  # as duas contas não confirmadas convivem; vale a que confirmar
        self.assertRedirects(response, reverse('verify_email'))
        self.client.post(reverse('verify_email'), {'code': last_code()})
        self.assertTrue(User.objects.get(username='joana').email_verified)
        self.assertFalse(User.objects.get(username='intruso').email_verified)

    def test_troca_de_email_para_endereco_preso(self):
        self.signup('intruso')
        self.client.logout()
        ana = make_user('ana')
        self.client.force_login(ana)
        data = {
            'first_name': 'Ana',
            'last_name': 'Lima',
            'email': '12546456@aluno.fsa.br',
            'institutional_id': '12546456',
        }
        self.assertRedirects(self.client.post(reverse('profile'), data), reverse('verify_email'))
        self.client.post(reverse('verify_email'), {'code': last_code()})
        ana.refresh_from_db()
        self.assertEqual(ana.email, '12546456@aluno.fsa.br')
        self.assertFalse(User.objects.filter(username='intruso').exists())

    def test_nova_senha_pelo_link_confirma_o_email(self):
        self.signup('joana')
        joana = User.objects.get(username='joana')
        self.client.logout()
        uid = urlsafe_base64_encode(force_bytes(joana.pk))
        token = default_token_generator.make_token(joana)
        url = reverse('password_reset_confirm', args=[uid, token])
        form_url = self.client.get(url).url  # o Django troca o token da URL pela sessão
        response = self.client.post(form_url, {'new_password1': 'Outra!senha987', 'new_password2': 'Outra!senha987'})
        self.assertRedirects(response, reverse('password_reset_complete'))
        joana.refresh_from_db()
        self.assertTrue(joana.email_verified and joana.student_id_confirmed)

    def test_limite_por_ip_comporta_uma_turma(self):
        for i in range(30):  # uma turma inteira se cadastrando no laboratório (mesmo IP)
            self.client.logout()
            self.signup(f'aluno{i}', email=f'{1000 + i}@aluno.fsa.br', document=str(1000 + i))
        self.assertEqual(len(mail.outbox), 30)


class ThrottleKeyTests(TestCase):
    def test_chave_nao_e_hash_simples(self):
        import hashlib

        from core import throttle

        throttle.hit(5, 60, 'login-ip', '10.0.0.7')
        key = Throttle.objects.get().key
        self.assertNotEqual(key, hashlib.sha256(b'login-ip:10.0.0.7').hexdigest())
        self.assertEqual(len(key), 64)


class AdminLogoutClearsDeviceTests(TestCase):
    def test_sair_pelo_admin_limpa_o_aparelho(self):
        admin_user = make_user('chefe', User.Role.ADMIN)
        User.objects.filter(pk=admin_user.pk).update(is_staff=True, is_superuser=True)
        self.client.force_login(admin_user)
        response = self.client.post(reverse('admin:logout'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Clear-Site-Data'], '"storage"')
        self.assertNotIn('_auth_user_id', self.client.session)
