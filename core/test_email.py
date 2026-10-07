import smtplib
from email.utils import parseaddr
from io import StringIO
from unittest import mock

from django.core.mail import EmailMessage, get_connection
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activities.test_utils import make_activity, make_user

from .crypto import SecretUnreadable
from .models import EmailSettings, SiteSettings

PLATFORM = 'core.mail.PlatformEmailBackend'


def configure(**overrides):
    config = EmailSettings.load()
    data = {
        'enabled': True,
        'host': 'relay.instituicao.br',
        'port': 587,
        'security': EmailSettings.Security.STARTTLS,
        'from_email': 'nao-responda@instituicao.br',
        'from_name': 'Eventos Fatec',
        'timeout': 7,
    }
    data.update(overrides)
    password = data.pop('password', None)
    for key, value in data.items():
        setattr(config, key, value)
    if password:
        config.set_password(password)
    config.save()
    return config


class EmailSettingsFormTests(TestCase):
    def setUp(self):
        self.url = reverse('email_settings')
        self.client.force_login(make_user('adm', User.Role.ADMIN))

    def post(self, **changes):
        data = {
            'enabled': 'on', 'host': 'relay.instituicao.br', 'port': 587, 'security': 'STARTTLS',
            'verify_certificate': 'on', 'username': '', 'password': '', 'from_email': 'nao-responda@instituicao.br',
            'from_name': '', 'reply_to': '', 'timeout': 15,
        }  # fmt: skip
        data.update(changes)
        return self.client.post(self.url, {k: v for k, v in data.items() if v is not None})

    def test_so_administrador(self):
        self.client.force_login(make_user('org', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(reverse('email_test'), {'recipient': 'a@b.com'}).status_code, 403)

    def test_relay_sem_autenticacao(self):
        response = self.post(security='NONE', port=25)
        self.assertEqual(response.status_code, 302)
        config = EmailSettings.load()
        self.assertTrue(config.enabled)
        self.assertEqual((config.host, config.port, config.security), ('relay.instituicao.br', 25, 'NONE'))
        self.assertFalse(config.has_password)

    def test_senha_criptografada_mantida_e_removida(self):
        self.post(username='smtpuser', password='S3nh@-Secreta')
        config = EmailSettings.load()
        self.assertNotIn('S3nh@-Secreta', config.password_encrypted)
        self.assertEqual(config.get_password(), 'S3nh@-Secreta')
        page = self.client.get(self.url)
        self.assertNotContains(page, 'S3nh@-Secreta')
        self.assertContains(page, 'Há uma senha cadastrada')
        self.post(username='smtpuser', from_name='Outro nome')  # senha em branco: mantém
        self.assertEqual(EmailSettings.load().get_password(), 'S3nh@-Secreta')
        response = self.post(username='smtpuser', clear_password='on')
        self.assertContains(response, 'Informe a senha do usuário do SMTP')
        self.post(username='', clear_password='on')
        self.assertFalse(EmailSettings.load().has_password)

    def test_validacoes(self):
        self.assertContains(self.post(host=''), 'Informe o servidor para ligar o envio')
        self.assertContains(self.post(from_email=''), 'Informe o e-mail do remetente')
        response = self.post(security='NONE', username='u', password='p')
        self.assertContains(response, 'a senha trafegaria aberta')
        # desligado pode ficar incompleto
        self.assertEqual(self.post(enabled=None, host='', from_email='').status_code, 302)

    def test_senha_ilegivel_apos_trocar_secret_key(self):
        configure(username='u', password='p')
        with override_settings(SECRET_KEY='outra-chave-completamente-diferente-0123456789-abcdef'):
            with self.assertRaises(SecretUnreadable):
                EmailSettings.load().get_password()


@override_settings(EMAIL_BACKEND=PLATFORM)
class PlatformBackendTests(TestCase):
    def message(self):
        return EmailMessage('Assunto', 'Corpo', to=['ana@exemplo.com'])

    def test_desligado_vai_para_o_log(self):
        stream = StringIO()
        with mock.patch('sys.stdout', stream):
            sent = get_connection().send_messages([self.message()])
        self.assertEqual(sent, 1)
        self.assertIn('Subject: Assunto', stream.getvalue())

    @mock.patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_starttls_com_autenticacao(self, smtp):
        configure(username='smtpuser', password='segredo', reply_to='secretaria@instituicao.br')
        SiteSettings.objects.update_or_create(pk=1, defaults={'institution_name': 'Fatec Ipiranga'})
        get_connection().send_messages([self.message()])
        smtp.assert_called_once()
        self.assertEqual(smtp.call_args.args, ('relay.instituicao.br', 587))
        self.assertEqual(smtp.call_args.kwargs['timeout'], 7)
        server = smtp.return_value
        server.starttls.assert_called_once()
        server.login.assert_called_once_with('smtpuser', 'segredo')
        from_addr, to_addrs, raw = server.sendmail.call_args.args
        self.assertEqual(parseaddr(from_addr)[1], 'nao-responda@instituicao.br')  # envelope SMTP
        self.assertEqual(to_addrs, ['ana@exemplo.com'])
        self.assertIn(b'From: Eventos Fatec <nao-responda@instituicao.br>', raw)
        self.assertIn(b'Reply-To: secretaria@instituicao.br', raw)

    @mock.patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_relay_sem_tls_nem_login(self, smtp):
        configure(security='NONE', port=25, from_name='')
        SiteSettings.objects.update_or_create(pk=1, defaults={'institution_name': 'Fatec Ipiranga'})
        get_connection().send_messages([self.message()])
        server = smtp.return_value
        server.starttls.assert_not_called()
        server.login.assert_not_called()
        self.assertIn(b'From: Fatec Ipiranga <nao-responda@instituicao.br>', server.sendmail.call_args.args[2])

    @mock.patch('django.core.mail.backends.smtp.smtplib.SMTP_SSL')
    def test_ssl_direto(self, smtp_ssl):
        configure(security='SSL', port=465)
        get_connection().send_messages([self.message()])
        self.assertEqual(smtp_ssl.call_args.args, ('relay.instituicao.br', 465))

    def test_certificado_autoassinado(self):
        configure(verify_certificate=False)
        import ssl

        from core.mail import build_backend

        context = build_backend(EmailSettings.load()).ssl_context
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)
        configure(verify_certificate=True)
        self.assertEqual(build_backend(EmailSettings.load()).ssl_context.verify_mode, ssl.CERT_REQUIRED)

    @mock.patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_confirmacao_de_inscricao_sai_pelo_relay(self, smtp):
        configure()
        activity = make_activity(make_user('org', User.Role.ORGANIZER))
        self.client.force_login(make_user('ana'))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('register_activity', args=[activity.id]))
        from_addr, to_addrs, raw = smtp.return_value.sendmail.call_args.args
        self.assertEqual(to_addrs, ['ana@exemplo.com'])
        self.assertIn(b'inscri', raw.lower())

    @mock.patch('django.core.mail.backends.smtp.smtplib.SMTP')
    def test_botao_de_teste(self, smtp):
        configure()
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        response = self.client.post(reverse('email_test'), {'recipient': 'adm@exemplo.com'}, follow=True)
        self.assertContains(response, 'E-mail de teste enviado para adm@exemplo.com')
        self.assertTrue(EmailSettings.load().last_test_ok)

        smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b'Authentication failed')
        configure(username='u', password='errada')
        response = self.client.post(reverse('email_test'), {'recipient': 'adm@exemplo.com'}, follow=True)
        self.assertContains(response, 'Falha no envio de teste')
        self.assertContains(response, 'SMTPAuthenticationError')
        config = EmailSettings.load()
        self.assertFalse(config.last_test_ok)
        self.assertIn('Authentication failed', config.last_test_message)

    def test_botao_de_teste_com_envio_desligado(self):
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        with mock.patch('sys.stdout', StringIO()):
            response = self.client.post(reverse('email_test'), {'recipient': 'adm@exemplo.com'}, follow=True)
        self.assertContains(response, 'está desligado')
