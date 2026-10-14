"""Correções P1 da auditoria: quebra de linha no assunto (QUA-01) e reservas em atividade encerrada (QUA-02)."""

from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.models import Activity
from activities.test_utils import local, make_activity, make_user, now
from core import outbox
from core.models import OutgoingEmail
from core.text import single_line
from registrations.models import Registration
from registrations.services import expire_all_offers


class SingleLineTests(TestCase):
    def test_junta_linhas_e_espacos(self):
        self.assertEqual(single_line('  Curso\r\nBcc: x@y.z \t fim '), 'Curso Bcc: x@y.z fim')
        self.assertEqual(single_line(None), '')

    def test_fila_normaliza_o_assunto_e_o_envio_funciona(self):
        with self.captureOnCommitCallbacks(execute=True):
            email = outbox.enqueue('ana@exemplo.com', 'Linha 1\nLinha 2', 'corpo')
        email.refresh_from_db()
        self.assertEqual(email.subject, 'Linha 1 Linha 2')
        self.assertEqual(email.status, OutgoingEmail.Status.SENT)
        self.assertEqual(mail.outbox[-1].subject, 'Linha 1 Linha 2')

    def test_titulo_da_atividade_com_quebra_e_gravado_em_uma_linha(self):
        org = make_user('org', User.Role.ORGANIZER)
        self.client.force_login(org)
        n = now()
        data = {
            'title': 'Curso\nBcc: x@y.z', 'description': 'd', 'activity_type': 'COURSE',
            'start_date': local(n + timedelta(days=2)), 'end_date': local(n + timedelta(days=3)),
            'registration_start': local(n - timedelta(days=1)), 'registration_end': local(n + timedelta(days=1)),
            'capacity': 5, 'status': 'PUBLISHED', 'min_attendance_percent': 75,
        }  # fmt: skip
        self.client.post(reverse('activity_create'), data)
        activity = Activity.objects.get()
        self.assertEqual(activity.title, 'Curso Bcc: x@y.z')
        ana = make_user('ana')
        self.client.force_login(ana)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('register_activity', args=[activity.id]))
        self.assertEqual(OutgoingEmail.objects.get().status, OutgoingEmail.Status.SENT)

    def test_assunto_do_comunicado_em_uma_linha(self):
        from activities.forms import AnnouncementForm

        form = AnnouncementForm({'audience': 'CONFIRMED', 'subject': 'Aviso\r\nimportante', 'body': 'Oi'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['subject'], 'Aviso importante')

    def test_titulo_do_lote_avulso_em_uma_linha(self):
        from certificates.forms import BatchForm

        today = timezone.localdate()
        data = {'title': 'Semana\nde TI', 'activity_type': 'EVENT'}
        form = BatchForm({**data, 'start_date': today, 'end_date': today, 'issue_date': today})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['title'], 'Semana de TI')


class OffersOnClosedActivityTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(
            self.org, start_date=now() + timedelta(days=5), end_date=now() + timedelta(days=5, hours=2)
        )

    def offer(self, minutes=-1):
        return Registration.objects.create(
            user=make_user(f'u{Registration.objects.count()}'),
            activity=self.activity,
            offer_expires_at=timezone.now() + timedelta(minutes=minutes),
        )

    def test_varredura_ignora_atividade_cancelada(self):
        registration = self.offer()
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.CANCELLED)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(expire_all_offers(), 0)
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertFalse(any('terminou' in m.subject for m in mail.outbox))

    def test_varredura_continua_valendo_para_publicada(self):
        registration = self.offer()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(expire_all_offers(), 1)
        registration.refresh_from_db()
        self.assertEqual(registration.status, Registration.Status.CANCELLED)

    def test_cancelar_atividade_tira_o_prazo_das_reservas(self):
        registration = self.offer(minutes=60)
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('activity_cancel', args=[self.activity.pk]))
        registration.refresh_from_db()
        self.assertIsNone(registration.offer_expires_at)

    def test_finalizar_atividade_tira_o_prazo_das_reservas(self):
        registration = self.offer(minutes=60)
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('activity_finish', args=[self.activity.pk]))
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.status, Activity.Status.FINISHED)
        registration.refresh_from_db()
        self.assertIsNone(registration.offer_expires_at)
