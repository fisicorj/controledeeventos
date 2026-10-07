import json
import uuid

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activities.models import Activity
from activities.test_utils import make_activity, make_session, make_user
from registrations.models import Registration

from .models import Attendance
from .views import client_ip


class QrCheckinTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.session = make_session(self.activity)
        self.registration = Registration.objects.create(user=make_user('p', first_name='Ana'), activity=self.activity)
        self.url = reverse('qr_checkin', args=[self.session.id])
        self.client.force_login(self.org)

    def scan(self, token):
        return self.client.post(self.url, json.dumps({'token': token}), content_type='application/json')

    def test_qr_valido(self):
        response = self.scan(f'ARENA-EVENTS:{self.registration.qr_token}')
        data = response.json()
        self.assertTrue(data['created'])
        self.assertEqual(data['registration_id'], self.registration.id)
        self.assertEqual(data['present_count'], 1)
        self.assertEqual(data['name'], 'Ana')
        self.assertFalse(self.scan(str(self.registration.qr_token)).json()['created'])
        self.assertEqual(Attendance.objects.count(), 1)

    def test_qr_de_fora_do_sistema(self):
        response = self.scan('https://exemplo.com')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])

    def test_qr_de_outra_atividade(self):
        self.assertEqual(self.scan(f'ARENA-EVENTS:{uuid.uuid4()}').status_code, 404)

    def test_check_in_fechado(self):
        self.session.check_in_open = False
        self.session.save()
        self.assertEqual(self.scan(str(self.registration.qr_token)).status_code, 400)

    def test_atividade_cancelada(self):
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.CANCELLED)
        response = self.scan(str(self.registration.qr_token))
        self.assertEqual(response.status_code, 400)
        self.assertIn('cancelado', response.json()['message'])
        manual = reverse('manual_checkin', args=[self.session.id, self.registration.id])
        self.assertContains(self.client.post(manual, follow=True), 'Check-in indisponível')
        self.assertFalse(Attendance.objects.exists())

    def test_participante_nao_faz_check_in(self):
        self.client.force_login(self.registration.user)
        self.assertEqual(self.scan(str(self.registration.qr_token)).status_code, 403)


class SessionPageTests(TestCase):
    def test_pagina_de_presenca(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org)
        session = make_session(activity, check_in_open=False)
        Registration.objects.create(user=make_user('p'), activity=activity)
        self.client.force_login(org)
        response = self.client.get(reverse('session_attendance', args=[session.id]))
        self.assertContains(response, 'Edite a sessão</a>')
        self.assertContains(response, "getElementById('filter')")  # busca funciona com check-in fechado
        self.assertNotContains(response, 'html5-qrcode')
        session.check_in_open = True
        session.save()
        response = self.client.get(reverse('session_attendance', args=[session.id]))
        self.assertContains(response, 'vendor/html5-qrcode/html5-qrcode.min.js')
        self.assertNotContains(response, 'location.reload')


class ClientIpTests(TestCase):
    def test_ignora_x_forwarded_for_sem_proxy(self):
        request = RequestFactory().get('/', HTTP_X_FORWARDED_FOR='6.6.6.6, 10.0.0.9', REMOTE_ADDR='127.0.0.1')
        self.assertEqual(client_ip(request), '127.0.0.1')
        with override_settings(NUM_PROXIES=1):
            self.assertEqual(client_ip(request), '10.0.0.9')
