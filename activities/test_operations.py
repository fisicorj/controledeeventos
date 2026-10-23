import json
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from attendance.models import Attendance
from core.models import SiteSettings
from registrations.models import Registration, RegistrationAnswer, SurveyResponse

from .models import Activity, Material, RegistrationQuestion, Speaker, SpotCategory
from .operations.duplication import _next_title, duplicate_activity
from .test_utils import make_activity, make_session, make_user, now

EMAIL = 'django.core.mail.backends.locmem.EmailBackend'


@override_settings(EMAIL_BACKEND=EMAIL)
class CategoryTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, capacity=3)
        self.alunos = SpotCategory.objects.create(activity=self.activity, name='Alunos', capacity=2, order=1)
        self.externos = SpotCategory.objects.create(activity=self.activity, name='Público externo', capacity=1, order=2)

    def register(self, user, category=None, code='', category_code=''):
        self.client.force_login(user)
        data = {'category': category.pk if category else '', 'access_code': code, 'category_code': category_code}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse('register_activity', args=[self.activity.id]), data, follow=True)

    def status(self, user):
        return Registration.objects.get(user=user, activity=self.activity)

    def test_pagina_mostra_os_tipos(self):
        page = self.client.get(self.activity.get_absolute_url())
        self.assertContains(page, 'Público externo')
        self.client.force_login(make_user('ana'))
        page = self.client.get(self.activity.get_absolute_url())
        self.assertContains(page, 'Alunos (2 vagas)')

    def test_escolha_obrigatoria(self):
        response = self.register(make_user('ana'))
        self.assertContains(response, 'Escolha o tipo de vaga.')
        self.assertFalse(Registration.objects.exists())

    def test_vagas_e_fila_por_tipo(self):
        a1, a2, a3, e1 = (make_user(n) for n in ('a1', 'a2', 'a3', 'e1'))
        self.register(a1, self.alunos)
        self.register(a2, self.alunos)
        self.register(a3, self.alunos)  # alunos lotado: fila, mesmo com vaga no externo
        self.assertEqual(self.status(a3).status, Registration.Status.WAITLISTED)
        self.register(e1, self.externos)
        self.assertEqual(self.status(e1).status, Registration.Status.CONFIRMED)
        self.assertEqual(self.status(e1).category, self.externos)
        # o externo desiste: a vaga é de externo, então a3 (aluno) continua na fila
        self.client.force_login(e1)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cancel_registration', args=[self.status(e1).pk]))
        self.assertEqual(self.status(a3).status, Registration.Status.WAITLISTED)
        self.assertIsNotNone(self.status(e1).cancelled_at)
        # um aluno desiste: a3 sobe
        self.client.force_login(a1)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cancel_registration', args=[self.status(a1).pk]))
        self.assertEqual(self.status(a3).status, Registration.Status.CONFIRMED)

    def test_aumentar_vagas_do_tipo_chama_a_fila(self):
        a1, a2, a3 = (make_user(n) for n in ('a1', 'a2', 'a3'))
        for user in (a1, a2, a3):
            self.register(user, self.alunos)
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('category_edit', args=[self.alunos.pk]),
                {'name': 'Alunos', 'capacity': 3, 'description': '', 'access_code': '', 'order': 1},
            )
        self.assertEqual(self.status(a3).status, Registration.Status.CONFIRMED)
        response = self.client.post(
            reverse('category_edit', args=[self.alunos.pk]),
            {'name': 'Alunos', 'capacity': 1, 'description': '', 'access_code': '', 'order': 1},
        )
        self.assertContains(response, 'Já há 3 inscritos confirmados')

    def test_tipo_so_com_matricula(self):
        self.alunos.requires_document = True
        self.alunos.save()
        response = self.register(make_user('sem'), self.alunos)
        self.assertContains(response, 'só para alunos da instituição')
        response = self.register(make_user('digitou', institutional_id='123'), self.alunos)  # sem e-mail institucional
        self.assertContains(response, 'só para alunos da instituição')
        SiteSettings.objects.update_or_create(pk=1, defaults={'student_email_domains': 'aluno.fsa.br'})
        aluno = make_user('com', institutional_id='123')
        User.objects.filter(pk=aluno.pk).update(email='123@aluno.fsa.br')
        aluno.refresh_from_db()
        self.register(aluno, self.alunos)
        self.assertEqual(Registration.objects.filter(status='CONFIRMED').count(), 1)

    def test_codigo_por_tipo_e_por_atividade(self):
        self.externos.access_code = 'TURMA8A'
        self.externos.save()
        response = self.register(make_user('x'), self.externos, category_code='errado')
        self.assertContains(response, 'exige o código deste tipo de vaga')
        self.register(make_user('y'), self.externos, category_code=' turma8a ')  # sem diferenciar maiúsculas
        self.assertEqual(Registration.objects.count(), 1)

        self.activity.access_code = 'SEGREDO'
        self.activity.save()
        self.assertContains(self.register(make_user('z'), self.alunos, ''), 'Código de acesso inválido')
        self.register(make_user('w'), self.alunos, 'segredo')
        self.assertEqual(Registration.objects.count(), 2)

    def test_codigo_do_evento_e_do_tipo_sao_diferentes(self):
        # Antes era um campo só: com códigos diferentes, ninguém conseguia entrar no tipo com código.
        self.activity.access_code = 'EVENTO'
        self.activity.save()
        self.externos.access_code = 'TURMA'
        self.externos.save()
        self.client.force_login(make_user('ana'))
        page = self.client.get(self.activity.get_absolute_url())
        self.assertContains(page, 'Código de acesso do evento')
        self.assertContains(page, 'Código do tipo de vaga')
        self.register(make_user('bia'), self.externos, 'evento', 'turma')
        self.assertEqual(Registration.objects.get().category, self.externos)

    def test_codigo_sem_tipos(self):
        SpotCategory.objects.all().delete()
        self.activity.access_code = 'ABC'
        self.activity.save()
        self.client.force_login(make_user('ana'))
        self.assertContains(self.client.get(self.activity.get_absolute_url()), 'Inscrição restrita')
        self.assertContains(self.register(make_user('bia'), None, 'x'), 'Código de acesso inválido')
        self.register(make_user('caio'), None, 'abc')
        self.assertEqual(Registration.objects.count(), 1)

    def test_gestao(self):
        self.register(make_user('a1'), self.alunos)
        self.client.force_login(self.org)
        page = self.client.get(reverse('activity_manage', args=[self.activity.id]))
        self.assertContains(page, '1 de 2')
        # tipo com inscrições não pode ser excluído
        response = self.client.post(reverse('category_delete', args=[self.alunos.pk]), follow=True)
        self.assertContains(response, 'já tem inscrições')
        self.client.post(reverse('category_delete', args=[self.externos.pk]))
        self.assertFalse(SpotCategory.objects.filter(pk=self.externos.pk).exists())
        response = self.client.post(
            reverse('category_create', args=[self.activity.id]),
            {'name': 'alunos', 'capacity': 1, 'order': 3},
        )
        self.assertContains(response, 'Já existe um tipo de vaga com este nome')
        response = self.client.post(
            reverse('category_create', args=[self.activity.id]), {'name': 'VIP', 'capacity': 50, 'order': 3}
        )
        self.assertContains(response, 'Não pode passar das vagas da atividade')

    def test_permissao(self):
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(reverse('category_create', args=[self.activity.id])).status_code, 403)
        self.assertEqual(self.client.get(reverse('category_edit', args=[self.alunos.pk])).status_code, 403)


class DuplicateTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, title='Semana de Engenharia 2026', workload_hours=12, access_code='X')
        speaker = Speaker.objects.create(activity=self.activity, name='Ana')
        session = make_session(self.activity, title='Abertura')
        session.speakers.add(speaker)
        RegistrationQuestion.objects.create(activity=self.activity, label='Curso', kind='CHOICE', choices='A\nB')
        SpotCategory.objects.create(activity=self.activity, name='Alunos', capacity=1)
        Registration.objects.create(user=make_user('ana'), activity=self.activity)

    def test_titulo_sugerido(self):
        self.assertEqual(_next_title('Semana de Engenharia 2026'), 'Semana de Engenharia 2027')
        self.assertEqual(_next_title('Workshop'), 'Workshop (nova edição)')

    def test_duplicar(self):
        self.client.force_login(self.org)
        page = self.client.get(reverse('activity_duplicate', args=[self.activity.id]))
        self.assertContains(page, 'value="Semana de Engenharia 2027"')
        start = timezone.localtime(self.activity.start_date + timedelta(days=365)).replace(second=0, microsecond=0)
        response = self.client.post(
            reverse('activity_duplicate', args=[self.activity.id]),
            {'title': 'Semana de Engenharia 2027', 'start_date': start.strftime('%Y-%m-%dT%H:%M')},
        )
        copy = Activity.objects.exclude(pk=self.activity.pk).get()
        self.assertRedirects(response, reverse('activity_edit', args=[copy.pk]))
        delta = copy.start_date - self.activity.start_date
        self.assertEqual(delta, start - self.activity.start_date)
        self.assertEqual(copy.registration_end - self.activity.registration_end, delta)
        self.assertEqual((copy.status, copy.slug), (Activity.Status.DRAFT, 'semana-de-engenharia-2027'))
        self.assertEqual((copy.workload_hours, copy.access_code, copy.previous_edition), (12, 'X', self.activity))
        session = copy.sessions.get()
        self.assertEqual(session.start_at - self.activity.sessions.get().start_at, delta)
        self.assertFalse(session.check_in_open)
        self.assertEqual([s.activity for s in session.speakers.all()], [copy])  # palestrante copiado, não o original
        self.assertEqual(copy.questions.count(), 1)
        self.assertEqual(copy.categories.get().name, 'Alunos')
        self.assertFalse(copy.registrations.exists())
        self.assertEqual(self.activity.speakers.count(), 1)

    def test_permissao(self):
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(reverse('activity_duplicate', args=[self.activity.id])).status_code, 403)


class StatsTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, capacity=4)
        self.session = make_session(self.activity)
        question = RegistrationQuestion.objects.create(
            activity=self.activity, label='Curso', kind='CHOICE', choices='ADS\nGTI'
        )
        users = [make_user(n, institutional_id='1' if n != 'ext' else '') for n in ('ana', 'bia', 'ext')]
        regs = [Registration.objects.create(user=u, activity=self.activity) for u in users]
        RegistrationAnswer.objects.create(registration=regs[0], question=question, value='ADS')
        RegistrationAnswer.objects.create(registration=regs[1], question=question, value='GTI')
        Attendance.objects.create(registration=regs[0], session=self.session, validated_by=self.org)
        SurveyResponse.objects.create(registration=regs[0], score=9)
        cancelled = Registration.objects.create(
            user=make_user('caio'), activity=self.activity, status='CANCELLED', cancelled_at=timezone.now()
        )
        self.assertTrue(cancelled.pk)

    def test_numeros(self):
        self.client.force_login(self.org)
        page = self.client.get(reverse('activity_stats', args=[self.activity.id]))
        n = page.context['n']
        self.assertEqual((n['confirmed'], n['cancelled'], n['registered']), (3, 1, 4))
        self.assertEqual((n['occupancy'], n['dropout'], n['attendance_rate']), (75, 25, 33))
        self.assertEqual(n['survey_avg'], 9.0)
        self.assertEqual(n['timeline'][-1]['total'], 4)
        titles = [b['title'] for b in n['breakdowns']]
        self.assertEqual(titles, ['Aluno ou externo', 'Curso'])
        curso = {i['label']: i['count'] for i in n['breakdowns'][1]['items']}
        self.assertEqual(curso, {'ADS': 1, 'GTI': 1, 'Sem resposta': 1})
        self.assertContains(page, 'Esta é a primeira edição')
        self.assertContains(page, 'Ver em tabela')

    def test_comparacao_entre_edicoes(self):
        nova = duplicate_activity(self.activity, 'Edição 2', self.activity.start_date + timedelta(days=30))
        self.client.force_login(self.org)
        page = self.client.get(reverse('activity_stats', args=[nova.id]))
        self.assertEqual([e['activity'] for e in page.context['comparison']], [self.activity, nova])
        self.assertContains(page, 'Comparação entre edições')

    def test_permissao(self):
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(reverse('activity_stats', args=[self.activity.id])).status_code, 403)


class BadgeTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, capacity=30)
        self.cat = SpotCategory.objects.create(activity=self.activity, name='Palestrante', capacity=5)
        for i in range(12):
            Registration.objects.create(
                user=make_user(f'p{i}', first_name=f'Pessoa{i}', last_name='da Silva Sauro'),
                activity=self.activity,
                category=self.cat if i == 0 else None,
            )
        self.client.force_login(self.org)
        self.url = reverse('activity_badges', args=[self.activity.id])

    def test_formulario_e_pdf(self):
        self.assertContains(self.client.get(self.url), 'Etiquetas 101,6 x 50,8 mm')
        for layout in ('a4-8', 'a4-10', 'carta-10'):
            response = self.client.get(self.url, {'layout': layout, 'pular': '3', 'dx': '1,5', 'dy': '-2'})
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertTrue(response.content.startswith(b'%PDF'))
        # 12 crachás + 3 pulados, 8 por folha = 2 páginas
        response = self.client.get(self.url, {'layout': 'a4-8', 'pular': '3'})
        self.assertEqual(response.content.count(b'/Type /Page\n') + response.content.count(b'/Type /Page\r'), 2)

    def test_so_uma_categoria(self):
        response = self.client.get(self.url, {'layout': 'a4-8', 'categoria': self.cat.pk})
        self.assertEqual(response.content.count(b'/Type /Page\n') + response.content.count(b'/Type /Page\r'), 1)

    def test_permissao(self):
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(self.url).status_code, 403)


class MaterialTests(TestCase):
    def setUp(self):
        self.private = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.private, ignore_errors=True)
        override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.private))
        override.enable()
        self.addCleanup(override.disable)
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(
            self.org, start_date=now() - timedelta(days=2), end_date=now() - timedelta(days=1),
            registration_start=now() - timedelta(days=5), registration_end=now() - timedelta(days=2),
        )  # fmt: skip
        self.session = make_session(self.activity)
        self.ana, self.bia, self.caio = make_user('ana'), make_user('bia'), make_user('caio')
        self.reg_ana = Registration.objects.create(user=self.ana, activity=self.activity)
        Registration.objects.create(user=self.bia, activity=self.activity)  # inscrita, mas faltou
        Attendance.objects.create(registration=self.reg_ana, session=self.session, validated_by=self.org)

    def add(self, **data):
        self.client.force_login(self.org)
        payload = {'title': 'Slides', 'audience': 'ATTENDED', 'order': 1, 'url': '', **data}
        if 'file' not in payload and not payload['url']:
            payload['file'] = SimpleUploadedFile('slides.pdf', b'%PDF-1.4 conteudo', content_type='application/pdf')
        self.client.post(reverse('material_create', args=[self.activity.id]), payload)
        return Material.objects.latest('id')

    def test_arquivo_privado_so_para_quem_participou(self):
        material = self.add()
        self.assertTrue(material.file.path.startswith(self.private))  # fora do /media/ público
        self.assertFalse(str(material.file.path).startswith(str(settings.MEDIA_ROOT)))
        url = reverse('material_open', args=[material.pk])

        self.client.force_login(self.ana)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4 conteudo')
        self.assertContains(self.client.get(reverse('my_registrations')), 'Materiais (1)')

        for user in (self.bia, self.caio):  # faltou; não inscrito
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)  # pede login

    def test_publico_todos_os_inscritos_e_link(self):
        material = self.add(audience='CONFIRMED', url='https://youtu.be/abc', file='')
        self.client.force_login(self.bia)
        response = self.client.get(reverse('material_open', args=[material.pk]))
        self.assertRedirects(response, 'https://youtu.be/abc', fetch_redirect_response=False)

    def test_liberacao_programada(self):
        later = timezone.localtime(timezone.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')
        material = self.add(available_from=later)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('material_open', args=[material.pk])).status_code, 404)
        self.client.force_login(self.org)  # a equipe confere antes
        self.assertEqual(self.client.get(reverse('material_open', args=[material.pk])).status_code, 200)

    def test_validacoes_e_remocao(self):
        self.client.force_login(self.org)
        response = self.client.post(
            reverse('material_create', args=[self.activity.id]), {'title': 'X', 'audience': 'ATTENDED', 'order': 1}
        )
        self.assertContains(response, 'Envie um arquivo ou informe um link')
        material = self.add()
        path = Path(material.file.path)
        self.assertTrue(path.exists())
        self.client.post(reverse('material_delete', args=[material.pk]))
        self.assertFalse(path.exists())

    def test_permissao_da_gestao(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('material_create', args=[self.activity.id])).status_code, 403)


class CheckinAppTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.session = make_session(self.activity, check_in_open=True)
        self.ana = make_user('ana', first_name='Ana', last_name='Lima')
        self.reg = Registration.objects.create(user=self.ana, activity=self.activity)
        self.client.force_login(self.org)

    def test_app_e_arquivos(self):
        self.assertContains(self.client.get(reverse('checkin_home')), self.session.title)
        page = self.client.get(reverse('checkin_app', args=[self.session.id]))
        self.assertContains(page, 'rel="manifest"')
        self.assertContains(page, 'js/checkin-sw.js')
        manifest = json.loads(self.client.get(reverse('checkin_manifest')).content)
        self.assertEqual((manifest['display'], manifest['scope']), ('standalone', '/attendance/'))
        sw = self.client.get(reverse('checkin_sw'))
        self.assertEqual(sw['Content-Type'], 'application/javascript')
        self.assertEqual(sw['Service-Worker-Allowed'], '/attendance/')
        self.assertIn(b"addEventListener('fetch'", sw.content)

    def test_lista_para_funcionar_offline(self):
        data = json.loads(self.client.get(reverse('checkin_roster', args=[self.session.id])).content)
        self.assertTrue(data['open'])
        person = data['people'][0]
        self.assertEqual(person['name'], 'Ana Lima')
        self.assertEqual(person['k'], str(self.reg.qr_token).replace('-', '')[:12])
        self.assertNotIn(str(self.reg.qr_token), json.dumps(data))  # o token inteiro não sai do servidor
        self.client.post(
            reverse('qr_checkin', args=[self.session.id]),
            json.dumps({'token': f'ARENA-EVENTS:{self.reg.qr_token}'}),
            content_type='application/json',
        )
        status = json.loads(self.client.get(reverse('checkin_status', args=[self.session.id])).content)
        self.assertEqual((status['present_count'], status['present']), (1, [self.reg.pk]))

    def test_permissao(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('checkin_home')).status_code, 403)
        self.assertEqual(self.client.get(reverse('checkin_app', args=[self.session.id])).status_code, 403)
        self.assertEqual(self.client.get(reverse('checkin_roster', args=[self.session.id])).status_code, 403)
        self.assertEqual(self.client.get(reverse('checkin_status', args=[self.session.id])).status_code, 403)
