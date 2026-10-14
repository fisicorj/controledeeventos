"""SEC-07 (CSP e Permissions-Policy) e SEC-05 (limpeza do check-in no aparelho)."""

import json
import re

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activities.test_utils import PASSWORD, make_activity, make_session, make_user
from registrations.models import Registration

INLINE_SCRIPT = re.compile(r'<script(?![^>]*\bsrc=)(?![^>]*type="application/(?:ld\+)?json")[^>]*>', re.I)
INLINE_HANDLER = re.compile(r'\son[a-z]+\s*=\s*["\']', re.I)


class CspTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.session = make_session(self.activity, check_in_open=True)
        Registration.objects.create(user=make_user('ana'), activity=self.activity)

    def test_politica_sem_script_inline(self):
        response = self.client.get(reverse('activity_list'))
        csp = response['Content-Security-Policy']
        script_src = next(d for d in csp.split(';') if d.strip().startswith('script-src'))
        self.assertNotIn('unsafe-inline', script_src)
        self.assertNotIn('unsafe-eval', script_src)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn(f'report-uri {reverse("csp_report")}', csp)

    def test_paginas_nao_tem_script_nem_evento_inline(self):
        self.client.force_login(self.org)
        urls = [
            reverse('home'), reverse('activity_list'), reverse('activity_detail', args=[self.activity.pk]),
            reverse('organizer_dashboard'), reverse('activity_manage', args=[self.activity.pk]),
            reverse('activity_edit', args=[self.activity.pk]), reverse('activity_stats', args=[self.activity.pk]),
            reverse('session_attendance', args=[self.session.pk]), reverse('checkin_home'),
            reverse('checkin_app', args=[self.session.pk]), reverse('batch_list'), reverse('batch_new'),
            reverse('batch_full'), reverse('profile'), reverse('my_registrations'), reverse('certificate_verify'),
            reverse('privacy'),
        ]  # fmt: skip
        admin = User.objects.create_superuser('root', 'root@exemplo.com', PASSWORD)
        for url in urls:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertFalse(INLINE_SCRIPT.search(html), INLINE_SCRIPT.search(html))
                self.assertFalse(INLINE_HANDLER.search(html), INLINE_HANDLER.search(html))
        self.client.force_login(admin)
        for url in (reverse('site_settings'), reverse('email_settings'), reverse('certificate_audit')):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertFalse(INLINE_SCRIPT.search(html))
                self.assertFalse(INLINE_HANDLER.search(html))

    @override_settings(CSP_MODE='report')
    def test_modo_relatorio(self):
        response = self.client.get(reverse('activity_list'))
        self.assertIn('Content-Security-Policy-Report-Only', response)
        self.assertNotIn('Content-Security-Policy', response)

    @override_settings(CSP_MODE='off')
    def test_desligada(self):
        self.assertNotIn('Content-Security-Policy', self.client.get(reverse('activity_list')))

    def test_camera_so_nas_telas_de_leitura(self):
        self.assertIn('camera=()', self.client.get(reverse('activity_list'))['Permissions-Policy'])
        self.client.force_login(self.org)
        for name in ('session_attendance', 'checkin_app'):
            policy = self.client.get(reverse(name, args=[self.session.pk]))['Permissions-Policy']
            self.assertIn('camera=(self)', policy)

    def test_relatorio_de_violacao_vai_para_o_log(self):
        report = {'blocked-uri': 'inline', 'document-uri': 'https://x/y', 'violated-directive': 'script-src'}
        body = {'csp-report': report}
        with self.assertLogs('arena.csp', 'WARNING') as logs:
            response = self.client.post(reverse('csp_report'), json.dumps(body), content_type='application/csp-report')
        self.assertEqual(response.status_code, 204)
        self.assertIn('script-src', logs.output[0])
        self.assertEqual(self.client.post(reverse('csp_report'), 'lixo', content_type='text/plain').status_code, 204)
        self.assertEqual(self.client.get(reverse('csp_report')).status_code, 405)


class CheckinDeviceCleanupTests(TestCase):
    def test_sair_pede_ao_navegador_para_apagar_os_dados(self):
        self.client.force_login(make_user('ana'))
        response = self.client.post(reverse('logout'))
        self.assertEqual(response['Clear-Site-Data'], '"storage"')

    def test_formularios_de_sair_tem_a_limpeza_do_app_js(self):
        self.client.force_login(make_user('ana'))
        html = self.client.get(reverse('my_registrations')).content.decode()
        self.assertIn('data-logout', html)
        self.assertIn('js/app.js', html)

    def test_service_worker_nao_guarda_a_tela_com_emails(self):
        self.client.force_login(make_user('org', User.Role.ORGANIZER))
        sw = self.client.get(reverse('checkin_sw')).content.decode()
        self.assertIn('OFFLINE_PAGES.test(url.pathname)', sw)
        self.assertIn('arena-checkin-v3', sw)
        self.assertIn('/static/vendor/bootstrap/bootstrap.min.css', sw)  # sem depender de outro site

    def test_csp_sem_dominios_de_terceiros(self):
        csp = self.client.get(reverse('home'))['Content-Security-Policy']
        self.assertNotIn('https://', csp)


class CspReportLogTests(TestCase):
    def test_quebra_de_linha_nao_forja_linha_no_log(self):
        # SEC-17 da 2ª auditoria
        report = {'blocked-uri': 'x\n2026-10-13 ERROR falso', 'document-uri': 'https://a/b', 'violated-directive': 's'}
        with self.assertLogs('arena.csp', 'WARNING') as logs:
            self.client.post(
                reverse('csp_report'), json.dumps({'csp-report': report}), content_type='application/csp-report'
            )
        self.assertNotIn('\n', logs.output[0])
        self.assertIn('x 2026-10-13 ERROR falso', logs.output[0])
