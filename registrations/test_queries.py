"""DES-03: "Minhas inscrições" com número fixo de consultas, qualquer que seja a quantidade de inscrições."""

from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import User
from activities.models import Material
from activities.test_utils import make_activity, make_user, now

from .models import Registration


class MyRegistrationsQueriesTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.me = make_user('eu')
        self.others = [make_user(f'outro{i}') for i in range(2)]

    def _add(self, waitlisted):
        activity = make_activity(self.org)
        Material.objects.create(
            activity=activity, title='Slides', url='https://exemplo.com/s', audience=Material.Audience.CONFIRMED,
            available_from=now() - timedelta(days=1),
        )  # fmt: skip
        if waitlisted:
            base = now() - timedelta(hours=1)
            for i, other in enumerate(self.others):
                Registration.objects.create(
                    user=other, activity=activity, status=Registration.Status.WAITLISTED,
                    waitlisted_at=base + timedelta(minutes=i),
                )  # fmt: skip
            Registration.objects.create(
                user=self.me, activity=activity, status=Registration.Status.WAITLISTED, waitlisted_at=now()
            )
        else:
            Registration.objects.create(user=self.me, activity=activity)

    def _count(self):
        self.client.force_login(self.me)
        self.client.get(reverse('my_registrations'))  # a primeira requisição carrega caches (sessão, configurações)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('my_registrations'))
        self.assertEqual(response.status_code, 200)
        return len(ctx), response

    def test_consultas_nao_crescem_com_as_inscricoes(self):
        self._add(waitlisted=False)
        self._add(waitlisted=True)
        few, _ = self._count()
        for _ in range(3):
            self._add(waitlisted=False)
            self._add(waitlisted=True)
        many, response = self._count()
        self.assertEqual(few, many)
        positions = [r.position for r in response.context['registrations'] if r.position]
        self.assertEqual(positions, [3, 3, 3, 3])
        self.assertTrue(all(r.materials for r in response.context['registrations'] if r.status == 'CONFIRMED'))
