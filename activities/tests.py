from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import User
from activities.models import Activity, Session
from core.models import SiteSettings
from registrations.models import Registration

from .test_utils import local, make_activity, make_session, make_user, now


class CatalogTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.future = make_activity(self.org, title='Futura')
        make_activity(
            self.org,
            title='Antiga',
            start_date=now() - timedelta(days=9),
            end_date=now() - timedelta(days=8),
            registration_start=now() - timedelta(days=20),
            registration_end=now() - timedelta(days=10),
        )
        make_activity(self.org, title='Rascunho', status=Activity.Status.DRAFT)

    def test_home_e_lista_mostram_so_futuras_publicadas(self):
        for url in (reverse('home'), reverse('activity_list')):
            response = self.client.get(url)
            self.assertContains(response, 'Futura')
            self.assertNotContains(response, 'Antiga')
            self.assertNotContains(response, 'Rascunho')

    def test_rascunho_nao_aparece_para_participante(self):
        draft = Activity.objects.get(title='Rascunho')
        self.assertEqual(self.client.get(reverse('activity_detail', args=[draft.id])).status_code, 404)
        self.client.force_login(make_user('p'))
        self.assertEqual(self.client.get(reverse('activity_detail', args=[draft.id])).status_code, 404)

    def test_organizador_pre_visualiza_rascunho(self):
        draft = Activity.objects.get(title='Rascunho')
        self.client.force_login(self.org)
        response = self.client.get(reverse('activity_detail', args=[draft.id]))
        self.assertContains(response, 'Pré-visualização')
        self.assertContains(response, 'Inscrições indisponíveis')


class RegistrationButtonTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.p = make_user('p')
        self.client.force_login(self.p)

    def detail(self, activity):
        return self.client.get(reverse('activity_detail', args=[activity.id]))

    def test_aberta(self):
        self.assertContains(self.detail(make_activity(self.org)), 'Inscrever-se')

    def test_ainda_nao_abriu(self):
        activity = make_activity(self.org, registration_start=now() + timedelta(hours=1))
        response = self.detail(activity)
        self.assertContains(response, 'Inscrições abrem em')
        self.assertNotContains(response, '>Inscrever-se<')

    def test_encerrada(self):
        activity = make_activity(self.org, registration_start=now() - timedelta(days=2), registration_end=now())
        self.assertContains(self.detail(activity), 'Inscrições encerradas.')

    def test_esgotada_sem_lista_de_espera(self):
        activity = make_activity(self.org, capacity=1, waitlist_enabled=False)
        Registration.objects.create(user=self.org, activity=activity)
        self.assertContains(self.detail(activity), 'Vagas esgotadas.')

    def test_ja_inscrito(self):
        activity = make_activity(self.org)
        Registration.objects.create(user=self.p, activity=activity)
        self.assertContains(self.detail(activity), 'Você está inscrito.')


class ActivityValidationTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.url = reverse('activity_edit', args=[self.activity.id])
        self.client.force_login(self.org)

    def post(self, **changes):
        a = self.activity
        data = {
            'title': a.title,
            'description': a.description,
            'activity_type': a.activity_type,
            'start_date': local(a.start_date),
            'end_date': local(a.end_date),
            'registration_start': local(a.registration_start),
            'registration_end': local(a.registration_end),
            'capacity': a.capacity,
            'location': '',
            'status': a.status,
            'waitlist_enabled': 'on',
            'certificate_enabled': 'on',
            'min_attendance_percent': 75,
        }
        data.update(changes)
        return self.client.post(self.url, data)

    def test_termino_antes_do_inicio(self):
        self.assertContains(self.post(end_date=local(now())), 'O término deve ser posterior ao início')

    def test_periodo_de_inscricao_invertido(self):
        response = self.post(registration_end=local(now() - timedelta(days=2)))
        self.assertContains(response, 'O fim das inscrições deve ser posterior')

    def test_inscricao_depois_do_termino(self):
        response = self.post(registration_end=local(now() + timedelta(days=5)))
        self.assertContains(response, 'devem encerrar até o término')

    def test_capacidade_menor_que_inscritos(self):
        Registration.objects.create(user=make_user('a'), activity=self.activity)
        Registration.objects.create(user=make_user('b'), activity=self.activity)
        self.assertContains(self.post(capacity=1), 'inscritos confirmados (2)')

    def test_edicao_valida(self):
        self.assertEqual(self.post(title='Novo título').status_code, 302)


class SessionTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.session = make_session(self.activity)
        self.client.force_login(self.org)

    def test_validacao_de_datas(self):
        url = reverse('session_create', args=[self.activity.id])
        start = self.activity.start_date
        cases = [
            (start + timedelta(hours=3), start + timedelta(hours=2), 'O término da sessão deve ser posterior'),
            (now(), now() + timedelta(hours=1), 'não pode começar antes'),
            (start + timedelta(days=1), start + timedelta(days=9), 'não pode terminar depois'),
        ]
        for begin, end, message in cases:
            response = self.client.post(url, {'title': 'X', 'start_at': local(begin), 'end_at': local(end)})
            self.assertContains(response, message)
        ok = self.client.post(
            url, {'title': 'X', 'start_at': local(start), 'end_at': local(start + timedelta(hours=1))}
        )
        self.assertEqual(ok.status_code, 302)

    def test_editar_sessao(self):
        url = reverse('session_edit', args=[self.session.id])
        start = self.activity.start_date
        response = self.client.post(url, {'title': 'Aula 1b', 'start_at': local(start), 'end_at': local(start)})
        self.assertContains(response, 'O término da sessão deve ser posterior')
        response = self.client.post(
            url, {'title': 'Aula 1b', 'start_at': local(start), 'end_at': local(start + timedelta(hours=1))}
        )
        self.assertEqual(response.status_code, 302)
        self.session.refresh_from_db()
        self.assertEqual(self.session.title, 'Aula 1b')
        self.assertFalse(self.session.check_in_open)

    def test_excluir_sessao(self):
        url = reverse('session_delete', args=[self.session.id])
        self.assertContains(self.client.get(url), 'Excluir sessão')
        self.client.post(url)
        self.assertFalse(Session.objects.exists())

    def test_outro_organizador_nao_mexe(self):
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        self.assertEqual(self.client.post(reverse('session_delete', args=[self.session.id])).status_code, 403)
        self.assertEqual(self.client.get(reverse('activity_manage', args=[self.activity.id])).status_code, 403)


class ManageTests(TestCase):
    def test_numero_de_consultas_nao_cresce_com_inscritos(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, capacity=50)
        make_session(activity)
        self.client.force_login(org)
        url = reverse('activity_manage', args=[activity.id])

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                response = self.client.get(url)
            return len(ctx.captured_queries), response

        SiteSettings.load()  # a primeira leitura cria o registro de configurações; não entra na contagem
        self.client.get(url)  # a primeira requisição registra a sessão (accounts/sessions.py); não entra na contagem
        Registration.objects.create(user=make_user('u0'), activity=activity)
        one, _ = count_queries()
        for i in range(1, 10):
            Registration.objects.create(user=make_user(f'u{i}'), activity=activity)
        ten, response = count_queries()
        self.assertEqual(one, ten)
        self.assertContains(response, '0 / 1', count=10)


class CancelDeleteTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.p = make_user('p')
        self.activity = make_activity(self.org)
        self.session = make_session(self.activity)
        self.client.force_login(self.org)

    def test_com_inscricao_so_cancela(self):
        Registration.objects.create(user=self.p, activity=self.activity)
        dashboard = self.client.get(reverse('organizer_dashboard'))
        self.assertContains(dashboard, reverse('activity_cancel', args=[self.activity.id]))
        self.assertNotContains(dashboard, reverse('activity_delete', args=[self.activity.id]))
        response = self.client.post(reverse('activity_delete', args=[self.activity.id]), follow=True)
        self.assertContains(response, 'não pode ser excluída')
        self.assertContains(self.client.get(reverse('activity_cancel', args=[self.activity.id])), '1 inscrito verá')
        self.client.post(reverse('activity_cancel', args=[self.activity.id]))
        self.activity.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.activity.status, Activity.Status.CANCELLED)
        self.assertFalse(self.session.check_in_open)
        self.assertEqual(Registration.objects.count(), 1)

    def test_sem_inscricao_exclui(self):
        self.client.post(reverse('activity_delete', args=[self.activity.id]))
        self.assertFalse(Activity.objects.exists())

    def test_admin_nao_exclui_com_inscricao(self):
        Registration.objects.create(user=self.p, activity=self.activity)
        self.client.force_login(User.objects.create_superuser('su', 'su@exemplo.com', 'Senha!forte123'))
        self.assertNotContains(self.client.get('/admin/activities/activity/'), 'delete_selected')
        self.assertEqual(self.client.get(f'/admin/activities/activity/{self.activity.id}/delete/').status_code, 403)


class SearchTests(TestCase):
    def setUp(self):
        org = make_user('org', User.Role.ORGANIZER)
        make_activity(org, title='Oficina de Linux', location='Lab 3')
        make_activity(
            org, title='Palestra de IA', activity_type=Activity.Type.EVENT, description='Inteligência artificial'
        )

    def test_busca_e_filtro(self):
        url = reverse('activity_list')
        response = self.client.get(url, {'q': 'linux'})
        self.assertContains(response, 'Oficina de Linux')
        self.assertNotContains(response, 'Palestra de IA')
        self.assertContains(response, '1 resultado')
        self.assertContains(self.client.get(url, {'q': 'inteligência'}), 'Palestra de IA')
        response = self.client.get(url, {'tipo': 'EVENT'})
        self.assertContains(response, 'Palestra de IA')
        self.assertNotContains(response, 'Oficina de Linux')
        self.assertContains(self.client.get(url, {'q': 'quântica'}), 'Nenhuma atividade encontrada')
        self.assertContains(
            self.client.get(url, {'tipo': 'INVALIDO'}), 'Oficina de Linux'
        )  # filtro inválido é ignorado

    def test_selos_de_situacao(self):
        response = self.client.get(reverse('activity_list'))
        self.assertContains(response, 'Vagas abertas')
