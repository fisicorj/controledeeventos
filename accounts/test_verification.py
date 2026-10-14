"""Confirmação de e-mail por código, matrícula validada pelo e-mail institucional, CPF e limites (B7 e B8)."""

import re

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activities.models import SpotCategory
from activities.test_utils import PASSWORD, make_activity, make_user
from core.models import SiteSettings, Throttle
from registrations.models import Registration

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def last_code():
    return re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)


@override_settings(EMAIL_BACKEND=LOCMEM)
class SignupVerificationTests(TestCase):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'student_email_domains': 'aluno.fsa.br'})

    def signup(self, email='12546456@aluno.fsa.br', document='12546456', username='joana'):
        return self.client.post(
            reverse('signup'),
            {'username': username, 'first_name': 'Joana', 'last_name': 'Rego', 'email': email,
             'institutional_id': document, 'password1': PASSWORD, 'password2': PASSWORD, 'accept_privacy': 'on'},
        )  # fmt: skip

    def test_aluno_digita_matricula_e_o_email_confirma(self):
        self.assertContains(self.client.get(reverse('signup')), '@aluno.fsa.br')
        response = self.signup()
        self.assertRedirects(response, reverse('verify_email'))
        user = User.objects.get(username='joana')
        self.assertFalse(user.email_verified)
        self.assertEqual(user.institutional_id, '12546456')
        self.assertFalse(user.student_id_confirmed)  # digitada, mas ainda sem confirmar o e-mail
        self.assertEqual(mail.outbox[-1].to, ['12546456@aluno.fsa.br'])
        self.assertIn('código de confirmação', mail.outbox[-1].subject)
        response = self.client.post(reverse('verify_email'), {'code': last_code()}, follow=True)
        self.assertContains(response, 'Matrícula 12546456 confirmada.')
        user.refresh_from_db()
        self.assertTrue(user.email_verified and user.student_id_confirmed)

    def test_matricula_precisa_bater_com_o_email(self):
        response = self.signup(document='99999999')
        self.assertContains(response, 'precisa ser a mesma do e-mail institucional')
        response = self.signup(document='')
        self.assertContains(response, 'Informe a sua matrícula')
        self.assertFalse(User.objects.exists())

    def test_matricula_com_email_pessoal_nao_vale(self):
        response = self.signup(email='joana@gmail.com', document='12546456')
        self.assertContains(response, 'Matrícula só com o e-mail institucional (@aluno.fsa.br)')
        self.assertFalse(User.objects.exists())

    def test_convidado_com_cpf(self):
        self.assertContains(self.signup(email='joana@gmail.com', document='111.444.777-36'), 'CPF inválido')
        self.signup(email='joana@gmail.com', document='11144477735')
        self.client.post(reverse('verify_email'), {'code': last_code()})
        user = User.objects.get(username='joana')
        self.assertEqual((user.email_verified, user.institutional_id), (True, '111.444.777-35'))
        self.assertFalse(user.student_id_confirmed)
        self.assertContains(self.client.get(reverse('profile')), 'pill-muted">CPF')

    def test_email_comum_sem_documento(self):
        self.signup(email='joana@gmail.com', document='')
        self.client.post(reverse('verify_email'), {'code': last_code()})
        user = User.objects.get(username='joana')
        self.assertTrue(user.email_verified)
        self.assertEqual(user.institutional_id, '')

    def test_codigo_errado_e_limite(self):
        self.signup()
        code = last_code()
        wrong = '000000' if code != '000000' else '111111'
        response = self.client.post(reverse('verify_email'), {'code': wrong})
        self.assertContains(response, 'Código incorreto. 4 tentativas restantes.')
        for _ in range(4):
            self.client.post(reverse('verify_email'), {'code': wrong})
        response = self.client.post(reverse('verify_email'), {'code': code})  # o certo, depois de esgotar
        self.assertContains(response, 'Muitas tentativas')
        self.assertFalse(User.objects.get(username='joana').email_verified)

    def test_reenvio_limitado(self):
        self.signup()  # 1º envio
        for _ in range(2):
            self.client.post(reverse('verify_email'), {'action': 'resend'})
        self.assertEqual(len(mail.outbox), 3)
        response = self.client.post(reverse('verify_email'), {'action': 'resend'}, follow=True)
        self.assertContains(response, 'Você pediu muitos códigos')
        self.assertEqual(len(mail.outbox), 3)
        # o código anterior deixa de valer quando sai um novo
        self.client.post(reverse('verify_email'), {'code': last_code()})
        self.assertTrue(User.objects.get(username='joana').email_verified)

    def test_sem_confirmar_nao_se_inscreve(self):
        self.signup(email='joana@gmail.com', document='')
        activity = make_activity(make_user('org', User.Role.ORGANIZER))
        self.assertContains(self.client.get(reverse('home')), 'Confirme o seu e-mail')
        response = self.client.post(reverse('register_activity', args=[activity.id]))
        self.assertRedirects(response, reverse('verify_email'))
        self.assertFalse(Registration.objects.exists())

    def test_contas_existentes_e_do_admin_continuam_valendo(self):
        self.assertTrue(make_user('antiga').email_verified)


@override_settings(EMAIL_BACKEND=LOCMEM)
class EmailChangeTests(TestCase):
    def setUp(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={'student_email_domains': 'aluno.fsa.br, fsa.br'})
        self.user = make_user('ana', first_name='Ana', last_name='Lima')
        self.client.force_login(self.user)

    def test_troca_so_vale_depois_de_confirmar(self):
        data = {'first_name': 'Ana', 'last_name': 'Lima', 'email': '777@aluno.fsa.br', 'institutional_id': '777'}
        response = self.client.post(reverse('profile'), data)
        self.assertRedirects(response, reverse('verify_email'))
        self.user.refresh_from_db()
        self.assertEqual((self.user.email, self.user.institutional_id), ('ana@exemplo.com', ''))  # ainda o antigo
        self.client.post(reverse('verify_email'), {'action': 'resend'})  # o reenvio guarda a matrícula digitada
        self.client.post(reverse('verify_email'), {'code': last_code()})
        self.user.refresh_from_db()
        self.assertEqual((self.user.email, self.user.institutional_id), ('777@aluno.fsa.br', '777'))
        self.assertContains(self.client.get(reverse('profile')), 'matrícula confirmada')

    def test_matricula_sem_email_institucional_nao_e_aceita(self):
        data = {'first_name': 'Ana', 'last_name': 'Lima', 'email': 'ana@exemplo.com', 'institutional_id': '999'}
        response = self.client.post(reverse('profile'), data)
        self.assertContains(response, 'Matrícula só com o e-mail institucional')
        self.client.post(reverse('profile'), {**data, 'institutional_id': '11144477735'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.institutional_id, '111.444.777-35')

    def test_vaga_so_de_alunos_exige_matricula_do_email(self):
        activity = make_activity(make_user('org', User.Role.ORGANIZER))
        alunos = SpotCategory.objects.create(activity=activity, name='Alunos', capacity=5, requires_document=True)
        response = self.client.post(reverse('register_activity', args=[activity.id]), {'category': alunos.pk})
        self.assertContains(response, 'com a matrícula confirmada pelo e-mail')

    def test_dominio_configuravel(self):
        site = SiteSettings.load()
        self.assertEqual(site.student_id_from_email('RA123@Aluno.FSA.br'), 'ra123')
        self.assertEqual(site.student_id_from_email('x@gmail.com'), '')
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        data = {f: getattr(site, f) for f in ('portal_name', 'primary_color', 'institution_name', 'city',
                'signer_name', 'signer_role', 'certificate_title', 'certificate_opening', 'certificate_place',
                'document_label', 'certificate_offset_mm')}  # fmt: skip
        response = self.client.post(reverse('site_settings'), {**data, 'student_email_domains': 'aluno fsa'})
        self.assertContains(response, 'não parece um domínio')
        self.client.post(reverse('site_settings'), {**data, 'student_email_domains': '@Aluno.FSA.br , fsa.br'})
        self.assertEqual(SiteSettings.load().student_email_domains, 'aluno.fsa.br, fsa.br')


@override_settings(EMAIL_BACKEND=LOCMEM)
class ThrottleTests(TestCase):
    def test_login(self):
        make_user('ana')
        for _ in range(5):
            response = self.client.post(reverse('login'), {'username': 'ana', 'password': 'errada'})
            self.assertContains(response, 'Usuário ou senha incorretos')
        response = self.client.post(reverse('login'), {'username': 'ana', 'password': PASSWORD})
        self.assertContains(response, 'Muitas tentativas de entrar')  # mesmo com a senha certa, espera
        self.assertNotIn('_auth_user_id', self.client.session)
        Throttle.objects.all().delete()  # passou o tempo
        response = self.client.post(reverse('login'), {'username': 'ana', 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)

    def test_recuperacao_de_senha(self):
        make_user('ana')
        for _ in range(5):
            response = self.client.post(reverse('password_reset'), {'email': 'ana@exemplo.com'})
            self.assertRedirects(response, reverse('password_reset_done'))  # a tela é sempre a mesma
        self.assertEqual(len(mail.outbox), 3)

    def test_codigo_de_acesso(self):
        activity = make_activity(make_user('org', User.Role.ORGANIZER), access_code='CERTO')
        self.client.force_login(make_user('ana'))
        url = reverse('register_activity', args=[activity.id])
        for _ in range(10):
            self.assertContains(self.client.post(url, {'access_code': 'errado'}), 'Código de acesso inválido')
        response = self.client.post(url, {'access_code': 'certo'})
        self.assertContains(response, 'Muitas tentativas com código errado')
        self.assertFalse(Registration.objects.exists())

    def test_validacao_de_certificado(self):
        for i in range(30):
            self.client.get(reverse('certificate_verify_code', args=[f'AAAA-BBBB-{i:04d}']))
        response = self.client.get(reverse('certificate_verify_code', args=['AAAA-BBBB-CCCC']))
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Muitas consultas', status_code=429)
        self.assertFalse(Throttle.objects.filter(key__contains='@').exists())  # chaves guardadas como hash
