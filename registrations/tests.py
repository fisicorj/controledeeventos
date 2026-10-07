from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from activities.models import Activity
from activities.test_utils import make_activity, make_user

from .models import Registration


class RegisterTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, capacity=1, waitlist_enabled=False)
        self.url = reverse('register_activity', args=[self.activity.id])

    def test_inscreve_e_respeita_lotacao(self):
        self.client.force_login(make_user('a'))
        self.client.post(self.url)
        self.assertEqual(Registration.objects.count(), 1)
        self.client.force_login(make_user('b'))
        response = self.client.post(self.url, follow=True)
        self.assertContains(response, 'alert-danger')  # mensagem de erro com a cor certa
        self.assertEqual(Registration.objects.count(), 1)

    def test_cancelar_e_reinscrever(self):
        user = make_user('a')
        self.client.force_login(user)
        self.client.post(self.url)
        registration = Registration.objects.get()
        self.client.post(reverse('cancel_registration', args=[registration.id]))
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.CANCELLED)
        self.client.post(self.url)
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)

    def test_get_nao_inscreve(self):
        self.client.force_login(make_user('a'))
        self.client.get(self.url)
        self.assertFalse(Registration.objects.exists())

    def test_qr_so_do_dono_e_com_atividade_publicada(self):
        owner = make_user('a')
        registration = Registration.objects.create(user=owner, activity=self.activity)
        url = reverse('registration_qr', args=[registration.id])
        self.client.force_login(make_user('intruso'))
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(owner)
        response = self.client.get(url)
        self.assertEqual(response['Content-Type'], 'image/png')
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.CANCELLED)
        self.assertEqual(self.client.get(url).status_code, 404)
        page = self.client.get(reverse('my_registrations'))
        self.assertContains(page, 'foi cancelada pela organização')
        self.assertNotContains(page, url)

    def test_atividade_fechada(self):
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.DRAFT)
        self.client.force_login(make_user('a'))
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertFalse(Registration.objects.exists())
