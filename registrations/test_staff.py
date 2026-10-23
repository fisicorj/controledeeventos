"""Ações da organização nas inscrições, prazo na fila da sessão, tarefas periódicas, cadastro e e-mail no log."""

from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.models import Activity
from activities.test_utils import make_activity, make_session, make_user
from attendance.models import Attendance

from .models import Registration, RegistrationLog, SessionEnrollment

S = SessionEnrollment.Status


class StaffSetup(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(
            self.org, title='Semana', activity_type=Activity.Type.EVENT, capacity=1, per_session=True,
            start_date=timezone.now() + timedelta(days=5), end_date=timezone.now() + timedelta(days=6),
            registration_end=timezone.now() + timedelta(days=4),
        )  # fmt: skip
        start = self.activity.start_date
        self.talk = make_session(self.activity, title='Palestra', start_at=start, end_at=start + timedelta(hours=1),
                                 capacity=1)  # fmt: skip
        self.ana = make_user('ana', email_verified=True)
        self.client.force_login(self.org)

    def post(self, url, data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, data, follow=True)


class StaffTests(StaffSetup):
    def test_incluir_participante_mesmo_lotado(self):
        Registration.objects.create(user=make_user('cheio'), activity=self.activity)
        mail.outbox.clear()
        response = self.post(reverse('participant_add', args=[self.activity.pk]), {'person': 'ana@exemplo.com'})
        self.assertContains(response, 'acima das vagas')
        registration = Registration.objects.get(user=self.ana)
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertEqual(registration.logs.get().action, RegistrationLog.Action.ADDED)
        self.assertEqual(mail.outbox[0].to, ['ana@exemplo.com'])
        response = self.post(reverse('participant_add', args=[self.activity.pk]), {'person': 'ana'})
        self.assertContains(response, 'já está inscrito')
        response = self.post(reverse('participant_add', args=[self.activity.pk]), {'person': 'ninguem'})
        self.assertContains(response, 'Não encontrei uma conta')

    def test_colocar_e_tirar_da_sessao_e_cancelar(self):
        registration = Registration.objects.create(user=self.ana, activity=self.activity)
        page = reverse('registration_admin', args=[registration.pk])
        response = self.post(page, {'action': 'add_session', 'session': self.talk.pk, 'note': 'Combinado.'})
        self.assertContains(response, 'confirmada em')
        self.assertEqual(SessionEnrollment.objects.get(registration=registration).status, S.CONFIRMED)
        mail.outbox.clear()
        response = self.post(page, {'action': 'remove_session', 'session': self.talk.pk})
        self.assertContains(response, 'Pessoa tirada de')
        self.assertEqual(SessionEnrollment.objects.get(registration=registration).status, S.CANCELLED)
        self.assertIn('você saiu da sessão', mail.outbox[0].subject)
        response = self.post(page, {'action': 'cancel', 'note': ''})
        self.assertContains(response, 'Informe o motivo')
        response = self.post(page, {'action': 'cancel', 'note': 'Duplicada.'})
        self.assertContains(response, 'Inscrição cancelada')
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.CANCELLED)
        self.assertContains(self.client.get(page), 'Duplicada.')
        self.assertEqual(registration.logs.count(), 3)

    def test_cancelar_chama_a_fila_e_recusa_com_certificado(self):
        from certificates.models import Certificate

        first = Registration.objects.create(user=self.ana, activity=self.activity)
        waiting = Registration.objects.create(
            user=make_user('bia'), activity=self.activity, status=Registration.Status.WAITLISTED,
            waitlisted_at=timezone.now(),
        )  # fmt: skip
        Activity.objects.filter(pk=self.activity.pk).update(waitlist_offer_hours=0)
        self.post(reverse('registration_admin', args=[first.pk]), {'action': 'cancel', 'note': 'Desistiu.'})
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, Registration.Status.CONFIRMED)
        Certificate.objects.create(
            registration=waiting, participant_name='Bia', activity_title='x', activity_type='Evento',
            start_date=timezone.now(), end_date=timezone.now(),
        )  # fmt: skip
        response = self.post(reverse('registration_admin', args=[waiting.pk]), {'action': 'cancel', 'note': 'x'})
        self.assertContains(response, 'já tem certificado')

    def test_so_quem_gerencia(self):
        registration = Registration.objects.create(user=self.ana, activity=self.activity)
        self.client.force_login(make_user('outro'))
        self.assertEqual(self.client.get(reverse('registration_admin', args=[registration.pk])).status_code, 403)
        response = self.client.post(reverse('participant_add', args=[self.activity.pk]), {'person': 'ana'})
        self.assertEqual(response.status_code, 403)


class SessionOfferTests(StaffSetup):
    def setUp(self):
        super().setUp()
        Activity.objects.filter(pk=self.activity.pk).update(capacity=10, waitlist_offer_hours=24)
        self.activity.refresh_from_db()
        self.talk.refresh_from_db()
        self.first = Registration.objects.create(user=self.ana, activity=self.activity)
        self.second = Registration.objects.create(user=make_user('bia'), activity=self.activity)
        SessionEnrollment.objects.create(registration=self.first, session=self.talk)
        self.waiting = SessionEnrollment.objects.create(
            registration=self.second, session=self.talk, status=S.WAITLISTED, waitlisted_at=timezone.now()
        )

    def leave_first(self):
        from . import sessions

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            sessions.leave(SessionEnrollment.objects.get(registration=self.first))
        self.waiting.refresh_from_db()

    def test_vaga_oferecida_com_prazo_e_confirmacao(self):
        self.leave_first()
        self.assertTrue(self.waiting.offer_pending)
        self.assertIn('confirme até', mail.outbox[0].subject)
        # Oferta pendente não faz check-in
        from .sessions import session_registrations

        self.assertFalse(session_registrations(self.talk).filter(pk=self.second.pk).exists())
        self.client.force_login(self.second.user)
        page = reverse('registration_sessions', args=[self.second.pk])
        self.assertContains(self.client.get(page), 'Confirmar vaga')
        response = self.client.post(page, {'session': self.talk.pk, 'action': 'confirm'}, follow=True)
        self.assertContains(response, 'Vaga confirmada')
        self.waiting.refresh_from_db()
        self.assertFalse(self.waiting.offer_pending)
        self.assertTrue(session_registrations(self.talk).filter(pk=self.second.pk).exists())

    def test_prazo_vencido_passa_para_o_proximo(self):
        from .services import expire_all_offers

        third = Registration.objects.create(user=make_user('caio'), activity=self.activity)
        next_in_line = SessionEnrollment.objects.create(
            registration=third, session=self.talk, status=S.WAITLISTED,
            waitlisted_at=timezone.now() + timedelta(seconds=1),
        )  # fmt: skip
        self.leave_first()
        SessionEnrollment.objects.filter(pk=self.waiting.pk).update(offer_expires_at=timezone.now() - timedelta(1))
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(expire_all_offers(), 1)
        self.waiting.refresh_from_db()
        next_in_line.refresh_from_db()
        self.assertEqual(self.waiting.status, S.CANCELLED)
        self.assertTrue(next_in_line.offer_pending)
        subjects = {m.to[0]: m.subject for m in mail.outbox}
        self.assertIn('terminou', subjects['bia@exemplo.com'])
        self.assertIn('confirme até', subjects['caio@exemplo.com'])

    def test_sem_prazo_na_atividade_confirma_na_hora(self):
        Activity.objects.filter(pk=self.activity.pk).update(waitlist_offer_hours=0)
        self.talk.activity.refresh_from_db()
        self.leave_first()
        self.assertEqual(self.waiting.status, S.CONFIRMED)
        self.assertFalse(self.waiting.offer_pending)

    def test_certificado_so_para_quem_confirmou(self):
        from certificates.models import Certificate
        from certificates.services import issue_certificates

        self.leave_first()
        Attendance.objects.create(registration=self.second, session=self.talk, validated_by=self.org)
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.FINISHED)
        self.activity.refresh_from_db()
        issue_certificates(self.activity)
        self.assertFalse(Certificate.objects.filter(session_enrollment=self.waiting).exists())


class PeriodicTaskTests(TestCase):
    def test_so_um_pega_a_vez(self):
        from core import periodic

        now = timezone.now()
        self.assertTrue(periodic.claim('reminders', 3600, now=now))
        self.assertFalse(periodic.claim('reminders', 3600, now=now + timedelta(minutes=10)))
        self.assertTrue(periodic.claim('reminders', 3600, now=now + timedelta(hours=1, seconds=1)))

    def test_reiniciar_o_worker_nao_repete_as_tarefas(self):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        from core.management.commands import process_outbox

        class Stop(Exception):
            pass

        for _ in range(2):  # dois "reinícios" seguidos
            with (
                mock.patch.object(process_outbox.Command, 'upkeep') as upkeep,
                mock.patch.object(process_outbox.time, 'sleep', side_effect=Stop),
                mock.patch.object(process_outbox, 'close_old_connections'),
            ):
                with self.assertRaises(Stop):
                    call_command('process_outbox', continuo=True, sem_zips=True, stdout=StringIO())
                calls = upkeep.call_count
            if _ == 0:
                self.assertEqual(calls, 1)
            else:
                self.assertEqual(calls, 0)


class SignupLimitTests(TestCase):
    @override_settings(SIGNUPS_PER_IP_HOUR=2)
    def test_limite_de_cadastros_por_ip(self):
        def signup(n):
            return self.client.post(
                reverse('signup'),
                {
                    'username': f'pessoa{n}', 'first_name': 'P', 'last_name': 'Q', 'email': f'p{n}@exemplo.com',
                    'password1': 'Senha!forte123', 'password2': 'Senha!forte123', 'accept_privacy': 'on',
                },
                follow=True,
            )  # fmt: skip

        for n in range(2):
            signup(n)
            self.client.logout()
        self.assertEqual(User.objects.count(), 2)
        response = signup(3)
        self.assertContains(response, 'Muitos cadastros feitos a partir desta rede')
        self.assertEqual(User.objects.count(), 2)


class EmailLogTests(TestCase):
    def test_envio_desligado_registra_so_destinatario_e_assunto(self):
        from django.core.mail import EmailMessage

        from core.mail import HeadersOnlyBackend, build_backend
        from core.models import EmailSettings

        config = EmailSettings.load()
        config.enabled = False
        with override_settings(EMAIL_LOG_BODY=False):
            backend = build_backend(config)
        self.assertIsInstance(backend, HeadersOnlyBackend)
        with self.assertLogs('core.mail', level='WARNING') as logs:
            backend.send_messages([EmailMessage('Código', 'O seu código é 123456', to=['a@exemplo.com'])])
        self.assertIn('a@exemplo.com', logs.output[0])
        self.assertNotIn('123456', logs.output[0])
        with override_settings(EMAIL_LOG_BODY=True):
            self.assertNotIsInstance(build_backend(config), HeadersOnlyBackend)
