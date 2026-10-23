"""V7-04 (inscrição pela organização exige e-mail confirmado), V7-05 (prazo do avaliador até o início do evento)
e o atalho de check-in no celular."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.test_utils import make_activity, make_session, make_user
from registrations.models import Registration


class VerifiedEmailTests(TestCase):
    def test_organizacao_nao_inscreve_conta_sem_email_confirmado(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org)
        make_user('semcodigo', email_verified=False)
        self.client.force_login(org)
        response = self.client.post(
            reverse('participant_add', args=[activity.pk]), {'person': 'semcodigo'}, follow=True
        )
        self.assertContains(response, 'ainda não confirmou o e-mail')
        self.assertFalse(Registration.objects.exists())

    def test_inscricao_na_hora_tambem_exige(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org)
        session = make_session(activity)
        make_user('semcodigo', email_verified=False)
        self.client.force_login(org)
        response = self.client.post(reverse('walk_in_checkin', args=[session.pk]), {'person': 'semcodigo'}, follow=True)
        self.assertContains(response, 'ainda não confirmou o e-mail')


class ReviewDeadlineClampTests(TestCase):
    def test_prazo_nao_passa_do_inicio_do_evento(self):
        from submissions.models import CallForPapers
        from submissions.services import due_date

        org = make_user('org', User.Role.ORGANIZER)
        now = timezone.now()
        activity = make_activity(org, start_date=now + timedelta(days=5), end_date=now + timedelta(days=6))
        call = CallForPapers.objects.create(
            activity=activity, opens_at=now - timedelta(days=1), closes_at=now + timedelta(days=4),
            instructions='x', review_days=14,
        )  # fmt: skip
        self.assertEqual(due_date(call, now), activity.start_date)
        call.review_days = 2
        self.assertEqual(due_date(call, now), now + timedelta(days=2))


class QuickCheckinTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        now = timezone.now()
        self.activity = make_activity(
            self.org, start_date=now - timedelta(hours=1), end_date=now + timedelta(hours=5),
            registration_end=now - timedelta(hours=2), registration_start=now - timedelta(days=2),
        )  # fmt: skip
        self.now_session = make_session(
            self.activity, title='Agora', start_at=now - timedelta(minutes=30), end_at=now + timedelta(hours=1)
        )
        self.later = make_session(
            self.activity, title='Depois', start_at=now + timedelta(hours=3), end_at=now + timedelta(hours=4)
        )

    def test_uma_sessao_agora_abre_o_leitor_direto(self):
        self.client.force_login(self.org)
        response = self.client.get(reverse('checkin_quick'))
        self.assertRedirects(response, reverse('checkin_app', args=[self.now_session.pk]))

    def test_varias_sessoes_agora_vao_para_a_lista_com_as_atuais_primeiro(self):
        now = timezone.now()
        make_session(
            self.activity, title='Paralela', start_at=now - timedelta(minutes=10), end_at=now + timedelta(hours=1)
        )
        self.client.force_login(self.org)
        self.assertRedirects(self.client.get(reverse('checkin_quick')), reverse('checkin_home'))
        page = self.client.get(reverse('checkin_home')).content.decode()
        self.assertLess(page.index('Agora</div>'), page.index('Depois</div>'))

    def test_equipe_de_check_in_ve_o_atalho_e_participante_nao(self):
        staff = make_user('equipe')
        self.activity.checkin_staff.add(staff)
        self.client.force_login(staff)
        page = self.client.get(reverse('activity_list'))
        self.assertContains(page, 'Ler QR Code')
        self.assertContains(page, reverse('checkin_quick'))
        self.client.force_login(make_user('ana'))
        page = self.client.get(reverse('activity_list'))
        self.assertNotContains(page, 'Ler QR Code')
        self.assertEqual(self.client.get(reverse('checkin_quick')).status_code, 403)

    def test_organizacao_tem_check_in_no_menu_e_atalho_no_app(self):
        self.client.force_login(self.org)
        page = self.client.get(reverse('organizer_dashboard'))
        self.assertContains(page, reverse('checkin_home'))
        manifest = self.client.get(reverse('checkin_manifest')).json()
        self.assertEqual(manifest['shortcuts'][0]['url'], reverse('checkin_quick'))
