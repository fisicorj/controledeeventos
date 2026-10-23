"""Acessibilidade das telas da organização (O-04): gestão da atividade, inscrições, números, chamada de trabalhos,
avaliação e check-in, com o axe-core num Chromium de verdade. Mesmos requisitos de core/test_browser.py."""

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.test import override_settings
from django.utils import timezone

from accounts.models import User
from activities.models import Activity
from activities.test_utils import make_activity, make_session, make_user
from registrations.models import Registration

from .test_browser import BrowserTestCase


class OrganizerAccessibilityTests(BrowserTestCase):
    def setUp(self):
        from submissions.models import CallForPapers, Review, Submission
        from submissions.tests import pdf

        private = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, private, ignore_errors=True)
        override = override_settings(PRIVATE_MEDIA_ROOT=Path(private))
        override.enable()
        self.addCleanup(override.disable)

        self.org = make_user('org', User.Role.ORGANIZER, first_name='Olga', last_name='Organiza')
        self.rev = make_user('rev', first_name='Rui', last_name='Revisor')
        ana = make_user('ana', first_name='Ana', last_name='Souza')
        now = timezone.now()
        self.activity = make_activity(
            self.org, title='Congresso de TI', activity_type=Activity.Type.EVENT,
            start_date=now - timedelta(hours=1), end_date=now + timedelta(days=1), capacity=50,
            certificate_enabled=True, workload_hours=8,
        )  # fmt: skip
        self.session = make_session(
            self.activity, title='Abertura', start_at=now - timedelta(hours=1), end_at=now + timedelta(hours=2)
        )
        self.registration = Registration.objects.create(
            user=ana, activity=self.activity, status=Registration.Status.CONFIRMED
        )
        call = CallForPapers.objects.create(
            activity=self.activity, opens_at=now - timedelta(days=5), closes_at=now + timedelta(days=5),
            instructions='Até 10 páginas.', areas='Redes\nSegurança', criteria='Originalidade\nClareza',
        )  # fmt: skip
        call.reviewers.add(self.rev)
        self.submission = Submission.objects.create(
            call=call, author=make_user('autora'), title='Redes em malha', abstract='Resumo.', area='Redes',
            file=pdf(),
        )  # fmt: skip
        self.review = Review.objects.create(submission=self.submission, reviewer=self.rev)

    def check_pages(self, page, pages):
        for label, url in pages:
            with self.subTest(label):
                page.goto(url)
                self.assertNotIn('Acesso restrito', page.content(), label)
                self.assert_accessible(page, label)

    def test_telas_da_organizacao(self):
        page = self.login('org')
        a = self.activity.pk
        self.check_pages(page, [
            ('painel do organizador', self.url('organizer_dashboard')),
            ('números do semestre', self.url('institution_panel')),
            ('gestão da atividade', self.url('activity_manage', a)),
            ('editar atividade', self.url('activity_edit', a)),
            ('números da atividade', self.url('activity_stats', a)),
            ('incluir em lote', self.url('participant_batch', a)),
            ('inscrição de uma pessoa', self.url('registration_admin', self.registration.pk)),
            ('chamada de trabalhos', self.url('call_manage', a)),
            ('editar a chamada', self.url('call_edit', a)),
            ('trabalho na organização', self.url('submission_manage', self.submission.pk)),
        ])  # fmt: skip

    def test_inscricao_em_lote_conferencia(self):
        page = self.login('org')
        page.goto(self.url('participant_batch', self.activity.pk))
        page.fill('#people', 'ana\nninguem@exemplo.com\nrev')
        page.get_by_role('button', name='Conferir a lista').click()
        page.wait_for_load_state()
        self.assertIn('Confira antes de inscrever', page.content())
        self.assert_accessible(page, 'conferência do lote')

    def test_telas_do_check_in(self):
        page = self.login('org')
        s = self.session.pk
        self.check_pages(page, [
            ('check-in: início', self.url('checkin_home')),
            ('presença da sessão', self.url('session_attendance', s)),
            ('leitor de QR Code', self.url('checkin_app', s)),
        ])  # fmt: skip

    def test_telas_do_avaliador(self):
        page = self.login('rev')
        self.check_pages(page, [
            ('trabalhos para avaliar', self.url('my_reviews')),
            ('formulário de avaliação', self.url('review_form', self.review.pk)),
        ])  # fmt: skip

    def test_telas_da_administracao(self):
        make_user('adm', User.Role.ADMIN, first_name='Ada', last_name='Admin')
        page = self.login('adm')
        self.check_pages(page, [
            ('configurações', self.url('site_settings')),
            ('configurações de e-mail', self.url('email_settings')),
            ('usuários', self.url('user_admin_list')),
            ('convites para a equipe', self.url('user_admin_invites')),
            ('manutenção', self.url('maintenance_status')),
            ('auditoria de certificados', self.url('certificate_audit')),
            ('emissão avulsa', self.url('batch_list')),
            ('nova emissão avulsa', self.url('batch_new')),
            ('relatório por turma', self.url('batch_group_report')),
            ('números do semestre (instituição)', self.url('institution_panel')),
            ('meus dados', self.url('profile')),
        ])  # fmt: skip

    def test_check_in_no_celular(self):
        """Largura de celular: o botão flutuante "Ler QR Code" e o menu recolhido entram na verificação."""
        page = self.login('org')
        page.set_viewport_size({'width': 390, 'height': 844})
        self.check_pages(page, [
            ('celular: gestão da atividade', self.url('activity_manage', self.activity.pk)),
            ('celular: check-in', self.url('checkin_home')),
            ('celular: presença da sessão', self.url('session_attendance', self.session.pk)),
        ])  # fmt: skip
        page.goto(self.url('organizer_dashboard'))
        self.assertTrue(page.get_by_role('link', name='Ler QR Code').is_visible())
