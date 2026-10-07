import csv
import io

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from attendance.models import Attendance
from registrations.models import Registration, RegistrationAnswer

from .models import RegistrationQuestion
from .test_utils import make_activity, make_session, make_user, now


class ReportTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, title='Semana de Tecnologia', capacity=1)
        self.s1 = make_session(self.activity, title='Abertura')
        self.s2 = make_session(self.activity, title='Palestras')
        question = RegistrationQuestion.objects.create(activity=self.activity, label='Curso', kind='TEXT')
        self.ana = make_user('ana', first_name='Ana', last_name='Souza', institutional_id='2024001')
        reg = Registration.objects.create(user=self.ana, activity=self.activity)
        RegistrationAnswer.objects.create(registration=reg, question=question, value='Análise e Desenv.')
        Attendance.objects.create(registration=reg, session=self.s1, validated_by=self.org)
        Registration.objects.create(
            user=make_user('bia'), activity=self.activity, status=Registration.Status.WAITLISTED, waitlisted_at=now()
        )
        self.client.force_login(self.org)

    def test_csv(self):
        response = self.client.get(reverse('report_registrations_csv', args=[self.activity.id]))
        self.assertIn('attachment', response['Content-Disposition'])
        text = response.content.decode('utf-8')
        self.assertTrue(text.startswith('﻿'))
        rows = list(csv.reader(io.StringIO(text.lstrip('﻿')), delimiter=';'))
        header = rows[0]
        self.assertEqual(header[:6], ['Nome', 'E-mail', 'Matrícula', 'Situação', 'Inscrito em', 'Curso'])
        self.assertTrue(header[6].startswith('Abertura'))
        self.assertEqual(header[-3:], ['Presenças', 'Frequência (%)', 'Certificado'])
        ana = rows[1]
        self.assertEqual(ana[0], 'Ana Souza')
        self.assertEqual(ana[2], '2024001')
        self.assertEqual(ana[5], 'Análise e Desenv.')
        self.assertEqual(ana[6:8], ['P', 'F'])
        self.assertEqual(ana[8:10], ['1', '50'])
        self.assertEqual(rows[2][3], 'Lista de espera')

    def test_pdfs(self):
        for url in (
            reverse('report_attendance_pdf', args=[self.activity.id]),
            reverse('report_signature_pdf', args=[self.s1.id]),
        ):
            response = self.client.get(url)
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertTrue(response.content.startswith(b'%PDF'))

    def test_outro_organizador_nao_acessa(self):
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        for url in (
            reverse('report_registrations_csv', args=[self.activity.id]),
            reverse('report_attendance_pdf', args=[self.activity.id]),
            reverse('report_signature_pdf', args=[self.s1.id]),
        ):
            self.assertEqual(self.client.get(url).status_code, 403)
