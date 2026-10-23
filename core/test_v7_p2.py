"""V7-01 (aviso de privacidade cobre os recursos novos) e V7-02 (aviso de endereço provisório nos certificados)."""

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activities.test_utils import make_activity, make_user
from core.checks import arena_deploy_checks, provisional_site_url


class PrivacyNoticeTests(TestCase):
    def test_aviso_cita_o_que_e_compartilhado(self):
        page = self.client.get(reverse('privacy'))
        for text in (
            'Palestrantes:',
            'nomes de quem teve presença',
            'coordenação da chamada',
            'e-mail dos coautores',
            'navegador como conhecido',
            'inscrever na hora',
            'cada sessão em que você esteve',
        ):
            with self.subTest(text):
                self.assertContains(page, text)


class ProvisionalUrlTests(TestCase):
    def test_reconhece_os_enderecos_da_aws(self):
        for url, expected in (
            ('https://d1abc2xyz.cloudfront.net', True),
            ('http://arena-events-test-alb-1.us-east-1.elb.amazonaws.com', True),
            ('https://eventos.fsa.br', False),
            ('http://127.0.0.1:8000', False),
        ):
            with self.subTest(url), override_settings(SITE_URL=url):
                self.assertEqual(provisional_site_url(), expected)

    @override_settings(SITE_URL='https://d1abc2xyz.cloudfront.net')
    def test_check_e_aviso_na_finalizacao(self):
        self.assertIn('arena.W005', [w.id for w in arena_deploy_checks(None)])
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, certificate_enabled=True, workload_hours=2)
        self.client.force_login(org)
        self.assertContains(self.client.get(reverse('activity_finish', args=[activity.pk])), 'endereço provisório')

    @override_settings(SITE_URL='https://eventos.fsa.br')
    def test_sem_aviso_com_dominio_proprio(self):
        self.assertNotIn('arena.W005', [w.id for w in arena_deploy_checks(None)])
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, certificate_enabled=True, workload_hours=2)
        self.client.force_login(org)
        self.assertNotContains(self.client.get(reverse('activity_finish', args=[activity.pk])), 'endereço provisório')
