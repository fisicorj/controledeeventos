"""Melhorias M-02 (agenda pessoal), M-06 (inscrição na hora pelo check-in), M-07 (relatório do palestrante) e
M-08 (pesquisa por sessão)."""

from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.models import Activity
from activities.test_utils import make_activity, make_session, make_user

from .models import Registration, SessionEnrollment

S = SessionEnrollment.Status


class PerSessionSetup(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        start = timezone.now() + timedelta(days=5)
        self.activity = make_activity(
            self.org, title='Semana', activity_type=Activity.Type.EVENT, capacity=10, per_session=True,
            start_date=start, end_date=start + timedelta(days=1), registration_end=start - timedelta(days=1),
        )  # fmt: skip
        self.talk = make_session(self.activity, title='Palestra', start_at=start, end_at=start + timedelta(hours=1))
        self.lab = make_session(
            self.activity, title='Laboratório', start_at=start + timedelta(hours=2), end_at=start + timedelta(hours=4)
        )
        self.ana = make_user('ana', email_verified=True)
        self.registration = Registration.objects.create(user=self.ana, activity=self.activity)


class PersonalCalendarTests(PerSessionSetup):
    def test_agenda_so_com_as_sessoes_escolhidas(self):
        SessionEnrollment.objects.create(registration=self.registration, session=self.lab, status=S.CONFIRMED)
        self.client.force_login(self.ana)
        response = self.client.get(reverse('registration_calendar', args=[self.registration.pk]))
        self.assertEqual(response['Content-Type'], 'text/calendar; charset=utf-8')
        body = response.content.decode()
        self.assertIn('Laboratório', body)
        self.assertNotIn('Palestra', body)
        self.assertEqual(body.count('BEGIN:VEVENT'), 1)
        self.assertContains(self.client.get(reverse('my_registrations')), 'Minhas sessões na agenda')

    def test_sem_sessao_escolhida_vai_o_evento_todo_e_so_o_dono_baixa(self):
        self.client.force_login(self.ana)
        body = self.client.get(reverse('registration_calendar', args=[self.registration.pk])).content.decode()
        self.assertEqual(body.count('BEGIN:VEVENT'), 2)
        self.client.force_login(make_user('bia'))
        self.assertEqual(
            self.client.get(reverse('registration_calendar', args=[self.registration.pk])).status_code, 404
        )

    def test_anexo_do_email_usa_a_agenda_pessoal(self):
        from core.outbox import ATTACHMENT_BUILDERS
        from registrations.notifications import my_ics_attachment

        self.assertIn('my_ics', ATTACHMENT_BUILDERS)

        SessionEnrollment.objects.create(registration=self.registration, session=self.talk, status=S.CONFIRMED)
        name, content, mime = my_ics_attachment(self.registration.pk)
        self.assertEqual((name, mime), ('semana.ics', 'text/calendar'))
        self.assertIn('Palestra', content)
        self.assertNotIn('Laboratório', content)


class WalkInTests(PerSessionSetup):
    def setUp(self):
        super().setUp()
        self.staff = make_user('equipe')
        self.activity.checkin_staff.add(self.staff)
        self.bia = make_user('bia', email_verified=True)
        self.url = reverse('walk_in_checkin', args=[self.talk.pk])

    def post(self, user, person):
        self.client.force_login(user)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, {'person': person}, follow=True)

    def test_equipe_inscreve_na_hora_com_presenca(self):
        from attendance.models import Attendance

        mail.outbox.clear()
        response = self.post(self.staff, 'bia@exemplo.com')
        self.assertContains(response, 'foi inscrito e a presença foi registrada')
        registration = Registration.objects.get(user=self.bia)
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        self.assertTrue(registration.session_enrollments.filter(session=self.talk, status=S.CONFIRMED).exists())
        self.assertTrue(Attendance.objects.filter(registration=registration, session=self.talk).exists())
        self.assertEqual(registration.logs.count(), 2)  # evento e sessão, com o motivo "na hora"
        self.assertTrue(any(m.to == ['bia@exemplo.com'] for m in mail.outbox))
        self.assertContains(self.post(self.staff, 'bia'), 'já está inscrito')

    def test_ja_inscrito_no_evento_entra_na_sessao(self):
        from attendance.models import Attendance

        response = self.post(self.staff, 'ana')
        self.assertContains(response, 'presença foi registrada')
        self.assertTrue(Attendance.objects.filter(registration=self.registration, session=self.talk).exists())

    def test_sem_vaga_so_a_organizacao(self):
        self.talk.capacity = 1
        self.talk.save()
        SessionEnrollment.objects.create(registration=self.registration, session=self.talk, status=S.CONFIRMED)
        response = self.post(self.staff, 'bia')
        self.assertContains(response, 'está lotada')
        self.assertFalse(Registration.objects.filter(user=self.bia, status='CONFIRMED').exists())
        response = self.post(self.org, 'bia')
        self.assertContains(response, 'acima das vagas')

    def test_quem_nao_e_da_equipe_nao_usa(self):
        self.client.force_login(make_user('curioso'))
        self.assertEqual(self.client.post(self.url, {'person': 'bia'}).status_code, 403)


class SessionSurveyAndReportTests(PerSessionSetup):
    def setUp(self):
        from activities.models import Speaker
        from attendance.models import Attendance

        super().setUp()
        SessionEnrollment.objects.create(registration=self.registration, session=self.talk, status=S.CONFIRMED)
        Attendance.objects.create(registration=self.registration, session=self.talk, validated_by=self.org)
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.FINISHED, survey_enabled=True)
        self.speaker = Speaker.objects.create(activity=self.activity, name='Paula Palestra', email='paula@exemplo.com')
        self.speaker.sessions.add(self.talk)

    def test_nota_por_sessao_na_pesquisa(self):
        from .models import SessionRating

        self.client.force_login(self.ana)
        url = reverse('survey', args=[self.registration.pk])
        page = self.client.get(url)
        self.assertContains(page, 'E as sessões em que você esteve')
        self.assertContains(page, 'Palestra')
        self.assertNotContains(page, 'Laboratório')  # sem presença
        self.client.post(
            url, {'score': 9, 'comment': 'Bom', f's{self.talk.pk}': 8, f'c{self.talk.pk}': 'Ótima palestra'}
        )
        rating = SessionRating.objects.get()
        self.assertEqual((rating.session, rating.score, rating.comment), (self.talk, 8, 'Ótima palestra'))
        self.client.force_login(self.org)
        manage = self.client.get(reverse('activity_manage', args=[self.activity.pk]))
        self.assertContains(manage, 'Notas por sessão')
        csv = self.client.get(reverse('survey_csv', args=[self.activity.pk])).content.decode()
        self.assertIn('Ótima palestra', csv)

    def test_nota_da_sessao_e_opcional(self):
        from .models import SessionRating, SurveyResponse

        self.client.force_login(self.ana)
        self.client.post(reverse('survey', args=[self.registration.pk]), {'score': 7})
        self.assertTrue(SurveyResponse.objects.exists())
        self.assertFalse(SessionRating.objects.exists())

    def test_relatorio_da_sessao_para_o_palestrante(self):
        from .models import SessionRating, SurveyResponse

        response = SurveyResponse.objects.create(registration=self.registration, score=9)
        SessionRating.objects.create(response=response, session=self.talk, score=10, comment='Excelente')
        self.client.force_login(self.org)
        page = self.client.get(reverse('session_report', args=[self.talk.pk]))
        self.assertContains(page, 'Relatório da sessão')
        self.assertContains(page, 'Excelente')
        self.assertNotContains(page, 'ana@exemplo.com')
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('session_report_send', args=[self.talk.pk]))
        self.assertEqual(mail.outbox[0].to, ['paula@exemplo.com'])
        body = mail.outbox[0].body
        self.assertIn('Presença: 1 de 1', body)
        self.assertIn('Excelente', body)
        self.assertNotIn('ana@exemplo.com', body)
        self.client.force_login(make_user('curioso'))
        self.assertEqual(self.client.get(reverse('session_report', args=[self.talk.pk])).status_code, 403)
