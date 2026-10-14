"""SEC-04: toda presença removida fica num registro permanente."""

from django.db import DatabaseError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from activities.test_utils import make_activity, make_session, make_user
from registrations.models import Registration

from .models import Attendance, AttendanceRemoval


class RemovalLogTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER, first_name='Olga')
        self.activity = make_activity(self.org)
        self.session = make_session(self.activity, title='Aula 1')
        self.ana = make_user('ana', first_name='Ana', last_name='Lima')
        self.registration = Registration.objects.create(user=self.ana, activity=self.activity)
        Attendance.objects.create(registration=self.registration, session=self.session, validated_by=self.org)
        self.url = reverse('remove_checkin', args=[self.session.id, self.registration.id])

    def test_remocao_pela_lista_fica_registrada_e_aparece_para_a_organizacao(self):
        self.client.force_login(self.org)
        self.client.post(self.url, REMOTE_ADDR='10.0.0.7')
        self.assertFalse(Attendance.objects.exists())
        log = AttendanceRemoval.objects.get()
        self.assertEqual(log.participant_name, 'Ana Lima (ana)')
        self.assertEqual(log.actor_id, self.org.pk)
        self.assertEqual(log.actor_name, 'Olga (org)')
        self.assertEqual(log.validated_by_name, 'Olga (org)')
        self.assertEqual(log.ip, '10.0.0.7')
        self.assertEqual((log.activity_id, log.session_id, log.registration_id),
                         (self.activity.pk, self.session.pk, self.registration.pk))  # fmt: skip
        self.assertIn('Aula 1', log.session_label)
        page = self.client.get(reverse('session_attendance', args=[self.session.id]))
        self.assertContains(page, 'Presenças removidas')
        self.assertContains(page, 'Ana Lima (ana)')

    def test_monitor_remove_e_fica_registrado_mas_nao_ve_o_historico(self):
        monitor = make_user('monitor')
        self.activity.checkin_staff.add(monitor)
        self.client.force_login(monitor)
        self.client.post(self.url)
        self.assertEqual(AttendanceRemoval.objects.get().actor_id, monitor.pk)
        page = self.client.get(reverse('session_attendance', args=[self.session.id]))
        self.assertNotContains(page, 'Presenças removidas')

    def test_sem_presenca_nao_registra(self):
        Attendance.objects.all().delete()
        self.client.force_login(self.org)
        self.client.post(self.url)
        self.assertFalse(AttendanceRemoval.objects.exists())

    def test_banco_recusa_alterar_ou_apagar_o_registro(self):
        self.client.force_login(self.org)
        self.client.post(self.url)
        with self.assertRaises(DatabaseError), transaction.atomic():
            AttendanceRemoval.objects.update(actor_name='outra pessoa')
        with self.assertRaises(DatabaseError), transaction.atomic():
            AttendanceRemoval.objects.all().delete()
        self.assertEqual(AttendanceRemoval.objects.get().actor_name, 'Olga (org)')

    def test_registro_sobrevive_a_exclusao_da_sessao(self):
        self.client.force_login(self.org)
        self.client.post(self.url)
        self.session.delete()
        self.assertTrue(AttendanceRemoval.objects.filter(session_label__contains='Aula 1').exists())

    def test_remocao_pelo_painel_administrativo_tambem_fica_registrada(self):
        admin = User.objects.create_superuser('root', 'root@exemplo.com', 'Senha!forte123', role=User.Role.ADMIN)
        self.client.force_login(admin)
        attendance = Attendance.objects.get()
        url = reverse('admin:attendance_attendance_delete', args=[attendance.pk])
        self.client.post(url, {'post': 'yes'})
        self.assertFalse(Attendance.objects.exists())
        self.assertEqual(AttendanceRemoval.objects.get().origin, 'Painel administrativo')
