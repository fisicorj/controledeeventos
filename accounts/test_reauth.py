"""SEC-19 da 2ª auditoria: confirmação de identidade antes de ações críticas, bloqueio da 2ª etapa por conta+IP
e reset da verificação encerrando as sessões."""

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import mfa
from accounts.models import User
from activities.test_utils import PASSWORD, make_user
from certificates.models import Certificate, CertificateBatch


def expire_reauth(client):
    session = client.session
    session['reauth_at'] = timezone.now().timestamp() - 3600
    session.save()


class ReauthTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        day = date(2026, 1, 10)
        self.batch = CertificateBatch.objects.create(title='X', start_date=day, end_date=day, issue_date=day,
                                                     created_by=self.org)  # fmt: skip
        moment = timezone.now()
        self.certificate = Certificate.objects.create(batch=self.batch, participant_name='Ana', activity_title='X',
                                                      activity_type='Evento', start_date=moment,
                                                      end_date=moment)  # fmt: skip
        self.client.force_login(self.org)
        self.url = reverse('batch_certificate_revoke', args=[self.batch.pk, self.certificate.code])

    def test_logo_depois_do_login_nao_pede_de_novo(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_sessao_antiga_pede_a_senha_antes_de_revogar(self):
        expire_reauth(self.client)
        response = self.client.get(self.url)
        self.assertRedirects(response, f'{reverse("reauth")}?next={self.url}', fetch_redirect_response=False)
        page = self.client.post(reverse('reauth'), {'password': 'errada', 'next': self.url})
        self.assertContains(page, 'Senha incorreta')
        response = self.client.post(reverse('reauth'), {'password': PASSWORD, 'next': self.url})
        self.assertRedirects(response, self.url)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_post_sem_confirmacao_nao_revoga(self):
        expire_reauth(self.client)
        self.client.post(self.url, {'reason': 'motivo do teste'})
        self.certificate.refresh_from_db()
        self.assertIsNone(self.certificate.revoked_at)

    def test_quem_usa_o_aplicativo_confirma_tambem_com_o_codigo(self):
        device = mfa.pending_device(self.org)
        mfa.confirm(device, mfa.totp(mfa.secret_of(device), mfa.current_step() - 1))
        session = self.client.session
        session[mfa.SESSION_KEY] = self.org.pk
        session.save()
        expire_reauth(self.client)
        page = self.client.post(reverse('reauth'), {'password': PASSWORD, 'code': '000000', 'next': self.url})
        self.assertContains(page, 'Código incorreto')
        # código de recuperação não vale aqui e também não é gasto (SEC-23 da 3ª auditoria)
        recovery = mfa.new_recovery_codes(device)
        device.refresh_from_db()
        before = list(device.recovery_codes)
        page = self.client.post(reverse('reauth'), {'password': PASSWORD, 'code': recovery[0], 'next': self.url})
        self.assertContains(page, 'Código incorreto')
        device.refresh_from_db()
        self.assertEqual(device.recovery_codes, before)
        code = mfa.totp(mfa.secret_of(device), mfa.current_step())
        response = self.client.post(reverse('reauth'), {'password': PASSWORD, 'code': code, 'next': self.url})
        self.assertRedirects(response, self.url)

    def test_configuracao_de_email_tambem_pede(self):
        admin = User.objects.create_superuser('root', 'root@exemplo.com', PASSWORD)
        self.client.force_login(admin)
        expire_reauth(self.client)
        self.assertEqual(self.client.get(reverse('email_settings')).status_code, 302)


@override_settings(MFA_REQUIRED=True)
class MfaLockoutTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.device = mfa.pending_device(self.org)
        mfa.confirm(self.device, mfa.totp(mfa.secret_of(self.device), mfa.current_step() - 1))

    def login(self, ip):
        self.client.post(reverse('login'), {'username': 'org', 'password': PASSWORD}, REMOTE_ADDR=ip)

    def test_erros_de_outro_ip_nao_bloqueiam_o_dono(self):
        self.login('10.0.0.66')
        right = mfa.totp(mfa.secret_of(self.device), mfa.current_step())
        wrong = '000000' if right != '000000' else '111111'
        for _ in range(5):
            self.client.post(reverse('mfa_verify'), {'code': wrong}, REMOTE_ADDR='10.0.0.66')
        self.assertContains(self.client.post(reverse('mfa_verify'), {'code': right}, REMOTE_ADDR='10.0.0.66'),
                            'Muitas tentativas')  # fmt: skip
        self.client.logout()
        self.login('10.0.0.5')
        response = self.client.post(reverse('mfa_verify'), {'code': right}, REMOTE_ADDR='10.0.0.5')
        self.assertEqual(response.status_code, 302)

    def test_reset_encerra_as_sessoes(self):
        self.client.force_login(self.org)
        other = self.client_class()
        other.force_login(self.org)
        out = StringIO()
        call_command('reset_mfa', 'org', stdout=out)
        self.assertIn('Sessões abertas encerradas: 2', out.getvalue())
        response = self.client.get(reverse('profile'))
        self.assertTrue(response['Location'].startswith(reverse('login')))  # voltou para o login


class ReauthCoverageTests(TestCase):
    """SEC-22 da 3ª auditoria: corrigir certificado, mudar a assinatura e editar lote também pedem confirmação."""

    def test_acoes_que_mudam_certificados_pedem_a_senha(self):
        admin = make_user('chefe', User.Role.ADMIN)
        batch = CertificateBatch.objects.create(
            title='Semana', start_date=date(2026, 1, 1), end_date=date(2026, 1, 1), issue_date=date(2026, 1, 1),
            created_by=admin,
        )  # fmt: skip
        moment = timezone.now()
        certificate = Certificate.objects.create(
            batch=batch, participant_name='Ana', activity_title='Semana', activity_type='Evento',
            start_date=moment, end_date=moment,
        )  # fmt: skip
        self.client.force_login(admin)
        urls = [
            reverse('certificate_correct', args=[certificate.code]),
            reverse('site_settings'),
            reverse('batch_edit', args=[batch.pk]),
        ]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)  # login recente: abre direto
        expire_reauth(self.client)
        for url in urls:
            response = self.client.get(url)
            self.assertRedirects(response, f'{reverse("reauth")}?next={url}', fetch_redirect_response=False)
            post = self.client.post(url, {'participant_name': 'Outra'}, HTTP_REFERER=url)
            self.assertTrue(post.url.startswith(reverse('reauth')), url)
        certificate.refresh_from_db()
        self.assertEqual(certificate.participant_name, 'Ana')
