"""Oportunidades da revisão v8: inscrição em lote (O-02), números do semestre (O-03) e menu com menos consultas
(O-05). A acessibilidade das telas da organização (O-04) fica em core/test_browser_organizer.py."""

from datetime import datetime, timedelta

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from attendance.models import Attendance
from registrations import batch
from registrations.models import Registration, SurveyResponse

from .models import Activity, SpotCategory
from .test_utils import make_activity, make_session, make_user


class BatchParseTests(TestCase):
    def test_lista_simples_e_csv_com_cabecalho(self):
        self.assertEqual(batch.parse('ana\n\n  bia@x.com \n'), ['ana', 'bia@x.com'])
        csv_text = 'Nome;E-mail;Turma\nAna Souza;ana@x.com;3B\nBia;bia@x.com;3B\n'
        self.assertEqual(batch.parse(csv_text), ['ana@x.com', 'bia@x.com'])
        self.assertEqual(batch.parse('usuario,nome\nana,Ana\n'), ['ana'])
        self.assertEqual(batch.parse('ana\tAna\nbia\tBia'), ['ana', 'bia'])  # colado de planilha

    def test_limite_de_linhas(self):
        with self.assertRaises(batch.TooMany):
            batch.parse('\n'.join(f'p{i}' for i in range(batch.MAX_ROWS + 1)))


class BatchEnrollTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, capacity=2)
        self.url = reverse('participant_batch', args=[self.activity.pk])
        self.ana = make_user('ana', email_verified=True)
        self.bia = make_user('bia', email_verified=True)
        self.caio = make_user('caio', email_verified=True)
        self.nao = make_user('naoconfirmou', email_verified=False)
        self.inativo = make_user('inativo', email_verified=True, is_active=False)
        Registration.objects.create(user=self.caio, activity=self.activity, status=Registration.Status.CONFIRMED)
        self.client.force_login(self.org)

    def test_confere_e_depois_inscreve(self):
        text = 'ana\nBIA@exemplo.com\ncaio\nnaoconfirmou\ninativo\nninguem@x.com\nana'
        response = self.client.post(self.url, {'people': text})
        self.assertContains(response, 'Confira antes de inscrever')
        rows = {row.typed: row.status for row in response.context['rows'][:-1]}
        self.assertEqual(rows, {
            'ana': batch.ADD, 'BIA@exemplo.com': batch.ADD, 'caio': batch.ALREADY,
            'naoconfirmou': batch.UNVERIFIED, 'inativo': batch.INACTIVE, 'ninguem@x.com': batch.NOT_FOUND,
        })  # fmt: skip
        self.assertEqual(response.context['rows'][-1].status, batch.REPEATED)
        self.assertEqual(response.context['over'], 1)  # 2 vagas, 1 ocupada, 2 a incluir
        self.assertFalse(Registration.objects.filter(user=self.ana).exists())  # só conferiu

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url, {'people': response.context['add_list'], 'confirm': '1', 'note': 'Turma 3B'}, follow=True
            )
        self.assertContains(response, '2 pessoa(s) inscrita(s)')
        for user in (self.ana, self.bia):
            registration = Registration.objects.get(user=user, activity=self.activity)
            self.assertEqual(registration.status, Registration.Status.CONFIRMED)
            self.assertEqual(registration.logs.get().note, 'Turma 3B')
        self.assertEqual(len(mail.outbox), 2)

    def test_arquivo_csv_e_tipo_de_vaga_obrigatorio(self):
        category = SpotCategory.objects.create(activity=self.activity, name='Alunos', capacity=10)
        upload = SimpleUploadedFile('turma.csv', 'email;nome\nana@exemplo.com;Ana\n'.encode('latin-1'))
        response = self.client.post(self.url, {'file': upload})
        self.assertContains(response, 'Escolha o tipo de vaga')
        upload = SimpleUploadedFile('turma.csv', 'email;nome\nana@exemplo.com;Ana\n'.encode('latin-1'))
        response = self.client.post(self.url, {'file': upload, 'category': category.pk})
        self.assertEqual([row.status for row in response.context['rows']], [batch.ADD])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url, {'people': 'ana@exemplo.com', 'confirm': '1', 'category': category.pk})
        self.assertEqual(Registration.objects.get(user=self.ana).category, category)

    def test_so_quem_gerencia_e_so_atividade_publicada(self):
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.org)
        self.activity.status = Activity.Status.DRAFT
        self.activity.save()
        self.assertRedirects(self.client.get(self.url), reverse('activity_manage', args=[self.activity.pk]))

    def test_lista_vazia(self):
        self.assertContains(self.client.post(self.url, {'people': '  '}), 'Cole a lista')


def dated(organizer, **data):
    """Atividade em data passada: as inscrições abrem e fecham antes dela."""
    data.setdefault('registration_start', data['start_date'] - timedelta(days=10))
    data.setdefault('registration_end', data['start_date'])
    data.setdefault('end_date', data['start_date'] + timedelta(hours=4))
    return make_activity(organizer, **data)


class InstitutionPanelTests(TestCase):
    def setUp(self):
        from certificates.models import Certificate

        self.org = make_user('org', User.Role.ORGANIZER)
        self.other = make_user('outra', User.Role.ORGANIZER)
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime(2026, 3, 10, 9), tz)
        self.a1 = dated(
            self.org,
            title='Semana de TI',
            start_date=start,
            end_date=start + timedelta(hours=8),
            status=Activity.Status.FINISHED,
            capacity=10,
            activity_type=Activity.Type.EVENT,
        )
        self.a2 = dated(
            self.other,
            title='Oficina',
            start_date=start + timedelta(days=30),
            end_date=start + timedelta(days=30, hours=4),
            status=Activity.Status.FINISHED,
            capacity=10,
        )
        dated(self.org, title='Rascunho', start_date=start, status=Activity.Status.DRAFT)
        dated(self.org, title='Outro semestre', start_date=start + timedelta(days=200),
                      )  # fmt: skip
        session = make_session(self.a1)
        people = [make_user(f'p{i}') for i in range(4)]
        for i, person in enumerate(people):
            reg = Registration.objects.create(user=person, activity=self.a1, status=Registration.Status.CONFIRMED)
            if i < 3:
                Attendance.objects.create(registration=reg, session=session, validated_by=self.org)
                Certificate.objects.create(
                    registration=reg, participant_name=person.username, activity_title=self.a1.title,
                    activity_type='Evento', start_date=self.a1.start_date, end_date=self.a1.end_date,
                    workload_hours=8,
                )  # fmt: skip
            SurveyResponse.objects.create(registration=reg, score=[10, 9, 7, 5][i])
        Registration.objects.create(user=people[0], activity=self.a2, status=Registration.Status.CONFIRMED)

    def get(self, user, period='2026-1'):
        self.client.force_login(user)
        return self.client.get(reverse('institution_panel'), {'semestre': period})

    def test_admin_ve_todas_as_atividades_do_semestre(self):
        response = self.get(make_user('adm', User.Role.ADMIN))
        totals = response.context['totals']
        self.assertEqual(totals['activities'], 2)
        self.assertEqual(totals['confirmed'], 5)
        self.assertEqual(totals['people'], 4)
        self.assertEqual(totals['attendance_rate'], 60)  # 3 de 5 nas atividades já iniciadas
        self.assertEqual(totals['hours'], 24)
        self.assertEqual(totals['certificates'], 3)
        self.assertEqual(totals['survey_avg'], 7.8)
        self.assertEqual(totals['nps'], 25)  # 2 promotores, 1 detrator, 4 respostas
        self.assertContains(response, 'Semana de TI')
        self.assertContains(response, 'Oficina')
        self.assertNotContains(response, 'Rascunho')
        self.assertNotContains(response, 'Outro semestre')

    def test_organizador_ve_so_as_proprias(self):
        response = self.get(self.org)
        self.assertEqual([row['activity'] for row in response.context['rows']], [self.a1])
        self.assertEqual(self.get(make_user('p', User.Role.PARTICIPANT)).status_code, 403)

    def test_csv(self):
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        response = self.client.get(reverse('institution_panel'), {'semestre': '2026-1', 'formato': 'csv'})
        body = response.content.decode('utf-8-sig')
        self.assertIn('Semana de TI;Evento;Finalizado;10/03/2026', body)
        self.assertIn(';4;3;75;3;24;4;7,8', body)

    def test_consultas_nao_crescem_com_o_numero_de_atividades(self):
        admin = make_user('adm', User.Role.ADMIN)
        self.client.force_login(admin)
        url = reverse('institution_panel')
        self.client.get(url, {'semestre': '2026-1'})  # a primeira guarda o menu na sessão
        with CaptureQueriesContext(connection) as few:
            self.client.get(url, {'semestre': '2026-1'})
        for i in range(5):
            dated(self.org, title=f'Extra {i}', start_date=self.a1.start_date)
        with CaptureQueriesContext(connection) as many:
            self.client.get(url, {'semestre': '2026-1'})
        self.assertEqual(len(few), len(many))


class MenuCacheTests(TestCase):
    def test_menu_fica_na_sessao_e_se_atualiza_na_tela_de_avaliacoes(self):
        from core.context_processors import MENU_KEY

        user = make_user('ana')
        self.client.force_login(user)
        self.client.get(reverse('activity_list'))
        self.assertIn(MENU_KEY, self.client.session)
        with CaptureQueriesContext(connection) as cached:
            self.client.get(reverse('activity_list'))
        session = self.client.session
        session.pop(MENU_KEY)
        session.save()
        with CaptureQueriesContext(connection) as fresh:
            self.client.get(reverse('activity_list'))
        self.assertLess(len(cached), len(fresh))

        session = self.client.session
        session[MENU_KEY] = {**session[MENU_KEY], 'reviews': {'show': True, 'pending': 7}}
        session.save()
        self.assertContains(self.client.get(reverse('activity_list')), '7 pendentes')  # vale o guardado
        response = self.client.get(reverse('my_reviews'))  # recalcula
        self.assertNotContains(response, '7 pendentes')

    def test_valor_de_outra_conta_nao_vale(self):
        from core.context_processors import MENU_KEY

        self.client.force_login(make_user('ana'))
        session = self.client.session
        session[MENU_KEY] = {'user': 999999, 'at': 10**12, 'reviews': {'show': True, 'pending': 7}, 'checkin': True}
        session.save()
        self.assertNotContains(self.client.get(reverse('activity_list')), '7 pendentes')
