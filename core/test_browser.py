"""Testes de navegador (APP-10): os fluxos principais num Chromium de verdade, com a verificação de
acessibilidade do axe-core nas telas mais usadas.

Precisam do Playwright e do Chromium:
    pip install -r requirements-dev.txt
    python -m playwright install chromium
Sem eles, os testes são pulados (o resto da suíte roda normalmente). A CI instala os dois.
Para rodar só estes: python manage.py test core.test_browser
"""

import os
from datetime import timedelta
from unittest import SkipTest

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.models import Activity
from activities.test_utils import PASSWORD, make_activity, make_session, make_user

# O axe aponta problemas com impacto "minor", "moderate", "serious" e "critical". Os dois últimos impedem
# ou atrapalham muito o uso com leitor de tela ou teclado: esses reprovam o teste.
BLOCKING = {'serious', 'critical'}


def _launch():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SkipTest('Playwright não instalado (pip install -r requirements-dev.txt).') from None
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch()
    except Exception as exc:  # navegador não baixado: python -m playwright install chromium
        playwright.stop()
        raise SkipTest(f'Chromium do Playwright indisponível: {str(exc).splitlines()[0]}') from None
    return playwright, browser


@tag('browser')
@override_settings(MFA_REQUIRED=False)
class BrowserTestCase(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        # O Playwright (API síncrona) roda um laço assíncrono na mesma thread; o Django bloquearia o ORM nos
        # testes sem esta variável. Vale só para o processo de teste.
        os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
        cls.playwright, cls.browser = _launch()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.browser.close()
        cls.playwright.stop()

    def new_page(self):
        context = self.browser.new_context(locale='pt-BR', bypass_csp=True)
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10_000)
        return page

    def url(self, name, *args):
        return self.live_server_url + reverse(name, args=args)

    def login(self, username):
        page = self.new_page()
        page.goto(self.url('login'))
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', PASSWORD)
        page.get_by_role('button', name='Entrar').click()
        page.wait_for_load_state()
        self.assertNotIn(reverse('login'), page.url, 'login não saiu da tela de entrada')
        return page

    def assert_accessible(self, page, label):
        from axe_playwright_python.sync_playwright import Axe

        violations = Axe().run(page).response['violations']
        blocking = [v for v in violations if v.get('impact') in BLOCKING]
        if blocking:
            report = '\n'.join(
                f'- {v["id"]} ({v["impact"]}): {v["help"]}\n    {node["target"][0]}: {node["failureSummary"]}'
                for v in blocking
                for node in v['nodes'][:3]
            )
            self.fail(f'Acessibilidade em {label}:\n{report}')


class MainFlowTests(BrowserTestCase):
    """Inscrição, check-in manual, finalização com emissão e download do certificado."""

    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER, first_name='Olga', last_name='Organiza')
        self.ana = make_user('ana', first_name='Ana', last_name='Souza', email_verified=True)
        start = timezone.now() + timedelta(days=1)
        self.activity = make_activity(
            self.org,
            title='Oficina de Python',
            activity_type=Activity.Type.COURSE,
            start_date=start,
            end_date=start + timedelta(hours=3),
            capacity=10,
            certificate_enabled=True,
            workload_hours=3,
        )
        self.session = make_session(
            self.activity, title='Mão na massa', start_at=start, end_at=start + timedelta(hours=3)
        )

    def test_inscricao_checkin_e_certificado(self):
        ana = self.login('ana')
        ana.goto(self.url('activity_detail', self.activity.pk))
        ana.get_by_role('button', name='Inscrever-se').click()
        ana.wait_for_load_state()
        ana.goto(self.url('my_registrations'))
        self.assertIn('Oficina de Python', ana.content())
        self.assert_accessible(ana, 'Minhas inscrições')

        org = self.login('org')
        org.goto(self.url('session_attendance', self.session.pk))
        org.get_by_role('row', name='Ana Souza').get_by_role('button', name='Registrar').click()
        org.wait_for_load_state()
        self.assertIn('Presença registrada', org.content())
        self.assert_accessible(org, 'presença da sessão')

        org.goto(self.url('activity_finish', self.activity.pk))
        org.get_by_role('button', name='Finalizar e emitir certificados').click()
        org.wait_for_load_state()
        self.assertIn('1 certificado(s) emitido(s)', org.content())
        self.assert_accessible(org, 'gestão da atividade')

        ana.goto(self.url('my_hours'))
        self.assert_accessible(ana, 'Minhas horas')
        from certificates.models import Certificate

        href = reverse('certificate_download', args=[Certificate.objects.get(registration__user=self.ana).code])
        ana.locator(f'a[href="{href}"]').wait_for()  # o link do certificado está na tela
        response = ana.request.get(self.live_server_url + href)
        self.assertEqual(response.headers['content-type'], 'application/pdf')


class PublicPagesAccessibilityTests(BrowserTestCase):
    """Telas que qualquer pessoa vê antes de entrar."""

    def test_telas_publicas(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, title='Semana de TI')
        page = self.new_page()
        for label, url in [
            ('início', self.url('home')),
            ('catálogo', self.url('activity_list')),
            ('página da atividade', self.url('activity_detail', activity.pk)),
            ('entrar', self.url('login')),
            ('criar conta', self.url('signup')),
            ('validar certificado', self.url('certificate_verify')),
        ]:
            with self.subTest(label):
                page.goto(url)
                self.assert_accessible(page, label)
