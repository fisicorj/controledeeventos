"""CloudFront na frente do ALB (infra/setup.sh, CLOUDFRONT=1): HTTPS pelo CloudFront-Forwarded-Proto e IP real."""

from django.test import RequestFactory, SimpleTestCase, override_settings

from core.http import client_ip


@override_settings(NUM_PROXIES=2, SECURE_PROXY_SSL_HEADER=('HTTP_CLOUDFRONT_FORWARDED_PROTO', 'https'))
class CloudFrontTests(SimpleTestCase):
    def request(self, **headers):
        return RequestFactory().get('/', **headers)

    def test_https_vem_do_cloudfront_e_nao_do_alb(self):
        # O ALB recebe HTTP do CloudFront e escreve "http" no X-Forwarded-Proto.
        request = self.request(HTTP_X_FORWARDED_PROTO='http', HTTP_CLOUDFRONT_FORWARDED_PROTO='https')
        self.assertTrue(request.is_secure())
        self.assertFalse(self.request(HTTP_X_FORWARDED_PROTO='https').is_secure())

    def test_ip_de_quem_acessa_e_o_penultimo(self):
        # navegador (forjado) -> CloudFront acrescenta o visitante -> ALB acrescenta o CloudFront
        request = self.request(HTTP_X_FORWARDED_FOR='6.6.6.6, 200.10.20.30, 130.176.1.1')
        self.assertEqual(client_ip(request), '200.10.20.30')


class ProxyHeaderSettingTests(SimpleTestCase):
    def test_nome_do_cabecalho_vira_chave_do_meta(self):
        import os
        import subprocess
        import sys

        env = {
            **os.environ,
            'NUM_PROXIES': '2',
            'PROXY_SSL_HEADER': 'CloudFront-Forwarded-Proto',
            'DJANGO_SETTINGS_MODULE': 'config.settings',
        }
        code = (
            'import django; django.setup(); from django.conf import settings; print(settings.SECURE_PROXY_SSL_HEADER)'
        )
        out = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, check=True)  # noqa: S603
        self.assertIn("('HTTP_CLOUDFRONT_FORWARDED_PROTO', 'https')", out.stdout)
