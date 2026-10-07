import re

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from activities.test_utils import PASSWORD, make_user


class SignUpTests(TestCase):
    def data(self, **changes):
        data = {
            'username': 'ana',
            'first_name': 'Ana',
            'last_name': 'Silva',
            'email': 'Ana@Exemplo.com',
            'password1': PASSWORD,
            'password2': PASSWORD,
        }
        data.update(changes)
        return data

    def test_cadastro_guarda_email_minusculo(self):
        self.client.post(reverse('signup'), self.data())
        self.assertEqual(User.objects.get().email, 'ana@exemplo.com')

    def test_email_repetido_com_maiusculas(self):
        User.objects.create_user('existente', 'ana@exemplo.com', PASSWORD)
        response = self.client.post(reverse('signup'), self.data(email='ANA@exemplo.com'))
        self.assertContains(response, 'Já existe uma conta com este e-mail.')
        self.assertEqual(User.objects.count(), 1)


class PasswordResetTests(TestCase):
    def test_fluxo_completo(self):
        user = make_user('ana')
        self.assertContains(self.client.get(reverse('login')), reverse('password_reset'))
        response = self.client.post(reverse('password_reset'), {'email': 'ANA@exemplo.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('redefinição de senha', mail.outbox[0].subject)
        link = re.search(r'https?://\S+', mail.outbox[0].body).group(0)
        path = link.split('testserver', 1)[1]
        response = self.client.get(path, follow=True)
        self.assertContains(response, 'Salvar nova senha')
        response = self.client.post(
            response.redirect_chain[-1][0],
            {'new_password1': 'OutraSenha!456', 'new_password2': 'OutraSenha!456'},
        )
        self.assertRedirects(response, reverse('password_reset_complete'))
        user.refresh_from_db()
        self.assertTrue(user.check_password('OutraSenha!456'))

    def test_email_inexistente_nao_revela(self):
        response = self.client.post(reverse('password_reset'), {'email': 'ninguem@exemplo.com'})
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)
