import csv
import io

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activities.models import Activity
from activities.test_utils import make_activity, make_session, make_user
from attendance.models import Attendance
from certificates.models import Certificate

from .models import Registration, SurveyResponse


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SurveyTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, min_attendance_percent=50, survey_required=True)
        session = make_session(self.activity)
        self.ana, self.bia, self.caio = make_user('ana'), make_user('bia'), make_user('caio')
        self.reg_ana = Registration.objects.create(user=self.ana, activity=self.activity)
        self.reg_bia = Registration.objects.create(user=self.bia, activity=self.activity)
        Registration.objects.create(user=self.caio, activity=self.activity)  # não compareceu
        for reg in (self.reg_ana, self.reg_bia):
            Attendance.objects.create(registration=reg, session=session, validated_by=self.org)

    def finish(self):
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('activity_finish', args=[self.activity.id]))
        self.activity.refresh_from_db()

    def answer(self, user, registration, score, comment=''):
        self.client.force_login(user)
        return self.client.post(
            reverse('survey', args=[registration.pk]), {'score': score, 'comment': comment}, follow=True
        )

    def test_convite_e_resposta(self):
        self.finish()
        recipients = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(recipients, ['ana@exemplo.com', 'bia@exemplo.com'])  # caio não compareceu
        self.assertIn('responda antes a pesquisa', mail.outbox[0].body)

        self.client.force_login(self.ana)
        page = self.client.get(reverse('survey', args=[self.reg_ana.pk]))
        self.assertContains(page, 'quanto você recomendaria')
        self.assertContains(self.client.post(reverse('survey', args=[self.reg_ana.pk]), {}), 'Escolha uma nota')
        response = self.answer(self.ana, self.reg_ana, 9, 'Muito bom')
        self.assertContains(response, 'Obrigado pela avaliação! Seu certificado já está liberado.')
        self.assertEqual(SurveyResponse.objects.get().score, 9)
        # não responde duas vezes
        self.assertContains(self.answer(self.ana, self.reg_ana, 2), 'já respondeu')
        self.assertEqual(SurveyResponse.objects.count(), 1)

    def test_quem_nao_participou_nao_avalia(self):
        self.finish()
        caio = Registration.objects.get(user=self.caio)
        self.assertContains(self.answer(self.caio, caio, 10), 'não está disponível para você')
        self.assertFalse(SurveyResponse.objects.exists())

    def test_antes_de_finalizar_nao_abre(self):
        self.assertContains(self.answer(self.ana, self.reg_ana, 10), 'não está disponível')

    def test_certificado_liberado_depois_da_resposta(self):
        self.finish()
        code = Certificate.objects.get(registration=self.reg_ana).code
        self.client.force_login(self.ana)
        mine = self.client.get(reverse('my_registrations'))
        self.assertContains(mine, 'Responda a avaliação para liberar o certificado')
        self.assertNotContains(mine, reverse('certificate_download', args=[code]))
        self.assertRedirects(
            self.client.get(reverse('certificate_download', args=[code])), reverse('survey', args=[self.reg_ana.pk])
        )
        # a equipe baixa normalmente
        self.client.force_login(self.org)
        self.assertEqual(self.client.get(reverse('certificate_download', args=[code])).status_code, 200)

        self.answer(self.ana, self.reg_ana, 10)
        self.assertEqual(self.client.get(reverse('certificate_download', args=[code])).status_code, 200)
        self.assertContains(self.client.get(reverse('my_registrations')), reverse('certificate_download', args=[code]))

    def test_sem_exigencia_o_certificado_sai_direto(self):
        Activity.objects.filter(pk=self.activity.pk).update(survey_required=False)
        self.finish()
        code = Certificate.objects.get(registration=self.reg_ana).code
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('certificate_download', args=[code])).status_code, 200)
        self.assertContains(self.client.get(reverse('my_registrations')), 'Avaliar a atividade')

    def test_pesquisa_desligada(self):
        Activity.objects.filter(pk=self.activity.pk).update(survey_enabled=False, survey_required=False)
        self.finish()
        self.assertTrue(all('/avaliacao/' not in m.body for m in mail.outbox))
        self.assertNotContains(self.client.get(reverse('activity_manage', args=[self.activity.id])), 'NPS')

    def test_resultado_para_a_organizacao(self):
        self.finish()
        self.answer(self.ana, self.reg_ana, 10, 'Excelente palestra')
        self.answer(self.bia, self.reg_bia, 4, 'Som ruim')
        self.client.force_login(self.org)
        page = self.client.get(reverse('activity_manage', args=[self.activity.id]))
        self.assertContains(page, '2 de 2 responderam (100%)')
        self.assertContains(page, '<span class="stat-value">7,0</span>')  # média
        self.assertContains(page, '<span class="stat-value">0</span>')  # NPS: 50% promotores - 50% detratores
        self.assertContains(page, 'Som ruim')
        self.assertNotContains(page, 'bia@exemplo.com</div></div>\n            </div>\n            <span class="pill')
        response = self.client.get(reverse('survey_csv', args=[self.activity.id]))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(rows[0], ['Data', 'Nota', 'Comentário'])  # sem nome de quem respondeu
        self.assertEqual([r[1] for r in rows[1:]], ['4', '10'])  # pela nota, não pela ordem de chegada
        self.assertNotIn(':', rows[1][0])  # só o dia, sem a hora

    def test_lembrete_para_quem_nao_respondeu(self):
        self.finish()
        self.answer(self.ana, self.reg_ana, 8)
        mail.outbox.clear()
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('survey_remind', args=[self.activity.id]), follow=True)
        self.assertContains(response, 'Convite reenviado para 1 pessoa.')
        self.assertEqual([m.to[0] for m in mail.outbox], ['bia@exemplo.com'])

    def test_permissoes_da_organizacao(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('survey_csv', args=[self.activity.id])).status_code, 403)
        self.assertEqual(self.client.post(reverse('survey_remind', args=[self.activity.id])).status_code, 403)
        # resposta de outra pessoa
        self.finish()
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('survey', args=[self.reg_bia.pk])).status_code, 404)
