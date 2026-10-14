"""B3 (cancelar depois do início), B4 (aviso de mudança de data ou local) e B6 (equipe de check-in)."""

import json
from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.test_utils import local, make_activity, make_session, make_user, now
from attendance.models import Attendance
from registrations.models import Registration


class CancelAfterStartTests(TestCase):
    def test_nao_cancela_depois_de_comecar(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(
            org, start_date=now() - timedelta(hours=1), registration_end=now() + timedelta(hours=1)
        )
        ana = make_user('ana')
        registration = Registration.objects.create(user=ana, activity=activity)
        self.client.force_login(ana)
        page = self.client.get(reverse('my_registrations'))
        self.assertContains(page, 'A atividade já começou')
        self.assertNotContains(page, reverse('cancel_registration', args=[registration.pk]))
        response = self.client.post(reverse('cancel_registration', args=[registration.pk]), follow=True)
        self.assertContains(response, 'não pode mais ser cancelada')
        registration.refresh_from_db()
        self.assertEqual(registration.status, 'CONFIRMED')
        # quem está só na lista de espera pode sair
        bia = make_user('bia')
        waiting = Registration.objects.create(user=bia, activity=activity, status='WAITLISTED', waitlisted_at=now())
        self.client.force_login(bia)
        self.client.post(reverse('cancel_registration', args=[waiting.pk]))
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, 'CANCELLED')


class ChangeNoticeTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, location='Sala 1', capacity=1)
        self.ana, self.bia = make_user('ana'), make_user('bia')
        self.reg = Registration.objects.create(user=self.ana, activity=self.activity, reminder_sent_at=timezone.now())
        Registration.objects.create(user=self.bia, activity=self.activity, status='WAITLISTED', waitlisted_at=now())
        self.client.force_login(self.org)
        self.url = reverse('activity_edit', args=[self.activity.id])

    def data(self, **changes):
        a = self.activity
        data = {'title': a.title, 'description': a.description, 'activity_type': a.activity_type,
                'start_date': local(a.start_date), 'end_date': local(a.end_date),
                'registration_start': local(a.registration_start), 'registration_end': local(a.registration_end),
                'capacity': a.capacity, 'location': a.location, 'status': a.status, 'waitlist_enabled': 'on',
                'certificate_enabled': 'on', 'min_attendance_percent': 75, 'notify_change': 'on'}  # fmt: skip
        data.update(changes)
        return data

    def test_mudanca_de_data_e_local_avisa(self):
        self.assertContains(self.client.get(self.url), 'Avisar os inscritos por e-mail')
        new_start = self.activity.start_date + timedelta(hours=2)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url, self.data(start_date=local(new_start), location='Auditório'), follow=True
            )
        self.assertContains(response, '2 inscritos avisados da mudança')
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['ana@exemplo.com', 'bia@exemplo.com'])
        body = next(m for m in mail.outbox if m.to == ['ana@exemplo.com']).body
        self.assertIn('Local: Auditório (antes: Sala 1)', body)
        self.assertIn('Início:', body)
        self.reg.refresh_from_db()
        self.assertIsNone(self.reg.reminder_sent_at)  # o lembrete sai de novo para a data nova

    def test_sem_mudanca_ou_sem_aviso_nao_envia(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, self.data(title='Outro título'))
            self.client.post(self.url, self.data(location='Lab 3', notify_change=''))
        self.assertEqual(len(mail.outbox), 0)
        self.reg.refresh_from_db()
        self.assertIsNotNone(self.reg.reminder_sent_at)  # data não mudou


class CheckinTeamTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.session = make_session(self.activity, check_in_open=False)
        self.monitor = make_user('monitor', first_name='Mona')
        self.ana = make_user('ana', first_name='Ana')
        self.registration = Registration.objects.create(user=self.ana, activity=self.activity)

    def test_organizador_monta_a_equipe(self):
        self.client.force_login(self.org)
        add = reverse('checkin_staff_add', args=[self.activity.id])
        response = self.client.post(add, {'person': 'ninguem'}, follow=True)
        self.assertContains(response, 'Não encontrei uma conta')
        response = self.client.post(add, {'person': 'MONITOR@exemplo.com'}, follow=True)
        self.assertContains(response, 'entrou na equipe de check-in')
        self.assertContains(response, 'Equipe de check-in')
        self.assertTrue(self.activity.checkin_staff.filter(pk=self.monitor.pk).exists())
        self.client.post(reverse('checkin_staff_remove', args=[self.activity.id, self.monitor.id]))
        self.assertFalse(self.activity.checkin_staff.exists())
        self.client.force_login(self.monitor)  # monitor não mexe na equipe
        self.assertEqual(self.client.post(add, {'person': 'ana'}).status_code, 403)

    def test_monitor_faz_check_in_e_nada_mais(self):
        self.activity.checkin_staff.add(self.monitor)
        self.client.force_login(self.monitor)
        self.assertContains(self.client.get(reverse('home')), reverse('checkin_home'))
        self.assertContains(self.client.get(reverse('checkin_home')), self.session.title)
        page = self.client.get(reverse('session_attendance', args=[self.session.id]))
        self.assertContains(page, 'Ana')
        self.assertNotContains(page, 'ana@exemplo.com')  # sem e-mails
        self.assertNotContains(page, reverse('activity_manage', args=[self.activity.id]))
        self.client.post(reverse('toggle_checkin', args=[self.session.id]))  # abre o check-in
        self.session.refresh_from_db()
        self.assertTrue(self.session.check_in_open)
        response = self.client.post(
            reverse('qr_checkin', args=[self.session.id]),
            json.dumps({'token': str(self.registration.qr_token)}),
            content_type='application/json',
        )
        self.assertTrue(response.json()['ok'])
        self.assertEqual(Attendance.objects.get().validated_by, self.monitor)
        roster = self.client.get(reverse('checkin_roster', args=[self.session.id])).json()
        self.assertNotIn('email', roster['people'][0])
        for name in ('activity_manage', 'activity_edit', 'activity_stats'):
            self.assertEqual(self.client.get(reverse(name, args=[self.activity.id])).status_code, 403)
        # outra atividade: nada
        other = make_session(make_activity(self.org, title='Outra'))
        self.assertEqual(self.client.get(reverse('session_attendance', args=[other.id])).status_code, 403)

    def test_participante_comum_nao_entra(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('checkin_home')).status_code, 403)
        self.assertEqual(self.client.post(reverse('toggle_checkin', args=[self.session.id])).status_code, 403)
