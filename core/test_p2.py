"""Melhorias P2 da auditoria: permissões centralizadas (ARQ-01), paginação (DES-01), regras de data no banco
(BD-01), faxina (BD-02) e operação (/saude/, lembretes e manutenção no worker)."""

import os
import time
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session as DjangoSession
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import EmailCode, User
from activities.models import Activity, Session
from activities.test_utils import make_activity, make_session, make_user, now
from certificates.models import Certificate, CertificateBatch
from core import maintenance, permissions
from core.http import client_ip
from core.models import Throttle
from core.pagination import semester_range


class PermissionsTests(TestCase):
    def test_regras_num_lugar_so(self):
        admin, org, other = make_user('adm', User.Role.ADMIN), make_user('org', User.Role.ORGANIZER), make_user('p')
        activity = make_activity(org)
        self.assertTrue(permissions.can_manage(admin, activity))
        self.assertTrue(permissions.can_manage(org, activity))
        self.assertFalse(permissions.can_manage(make_user('org2', User.Role.ORGANIZER), activity))
        self.assertFalse(permissions.can_manage(other))
        self.assertFalse(permissions.can_checkin(other, activity))
        activity.checkin_staff.add(other)
        self.assertTrue(permissions.can_checkin(other, activity))
        self.assertTrue(permissions.is_organizer(org) and not permissions.is_platform_admin(org))
        self.assertEqual(list(permissions.activities_for(org, Activity.objects.all())), [activity])

    def test_client_ip_em_core_http(self):
        request = RequestFactory().get('/', REMOTE_ADDR='10.1.1.1', HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(client_ip(request), '10.1.1.1')
        with override_settings(NUM_PROXIES=1):
            self.assertEqual(client_ip(request), '1.2.3.4')


class PaginationTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.client.force_login(self.org)

    def test_painel_paginado_e_filtrado_por_semestre_e_situacao(self):
        base = timezone.make_aware(timezone.datetime(2025, 3, 10, 10))
        for i in range(22):
            make_activity(self.org, title=f'Antiga {i}', start_date=base, end_date=base + timedelta(hours=2),
                          registration_start=base - timedelta(days=5), registration_end=base)  # fmt: skip
        make_activity(self.org, title='Atual', status=Activity.Status.DRAFT)
        page = self.client.get(reverse('organizer_dashboard'))
        self.assertEqual(len(page.context['activities']), 20)
        self.assertContains(page, 'Página 1 de 2')
        self.assertContains(page, '<option value="2025-1"')
        self.assertContains(self.client.get(reverse('organizer_dashboard') + '?pagina=2'), 'Antiga')
        filtered = self.client.get(reverse('organizer_dashboard') + '?semestre=2025-1&pagina=2')
        self.assertEqual(len(filtered.context['activities']), 2)
        self.assertContains(filtered, 'semestre=2025-1&pagina=1')
        drafts = self.client.get(reverse('organizer_dashboard') + '?situacao=DRAFT')
        self.assertEqual([a.title for a in drafts.context['activities']], ['Atual'])
        self.assertEqual(self.client.get(reverse('organizer_dashboard') + '?semestre=xx').status_code, 200)

    def test_semestre(self):
        start, end = semester_range('2026-2')
        self.assertEqual((start.month, end.year, end.month), (7, 2027, 1))
        self.assertIsNone(semester_range('2026-3'))
        self.assertIsNone(semester_range('abc'))

    def test_catalogo_paginado(self):
        for i in range(14):
            make_activity(self.org, title=f'Curso {i}')
        page = self.client.get(reverse('activity_list'))
        self.assertEqual(len(page.context['activities']), 12)
        self.assertEqual(len(self.client.get(reverse('activity_list') + '?pagina=2').context['activities']), 2)

    def test_emissoes_e_certificados_do_lote_paginados(self):
        day = date(2026, 1, 10)
        for i in range(21):
            CertificateBatch.objects.create(title=f'Evento {i}', start_date=day, end_date=day, issue_date=day,
                                            created_by=self.org)  # fmt: skip
        listing = self.client.get(reverse('batch_list'))
        self.assertEqual(len(listing.context['batches']), 20)
        self.assertEqual(len(self.client.get(reverse('batch_list') + '?q=Evento 2').context['batches']), 2)
        batch = CertificateBatch.objects.first()
        moment = timezone.now()
        for i in range(105):
            Certificate.objects.create(batch=batch, participant_name=f'Pessoa {i:03}', participant_document=str(i),
                                       activity_title='X', activity_type='Evento', start_date=moment,
                                       end_date=moment)  # fmt: skip
        detail = self.client.get(reverse('batch_detail', args=[batch.pk]))
        self.assertEqual(len(detail.context['page'].object_list), 100)
        self.assertContains(detail, 'Certificados (105)')
        found = self.client.get(reverse('batch_detail', args=[batch.pk]) + '?busca=pessoa 104')
        self.assertEqual([c.participant_name for c in found.context['page'].object_list], ['Pessoa 104'])


class DateConstraintTests(TestCase):
    def test_banco_recusa_datas_invertidas(self):
        activity = make_activity(make_user('org', User.Role.ORGANIZER))
        cases = [
            (Activity.objects.filter(pk=activity.pk), {'end_date': activity.start_date}),
            (Activity.objects.filter(pk=activity.pk), {'registration_end': activity.registration_start}),
            (Activity.objects.filter(pk=activity.pk), {'registration_end': activity.end_date + timedelta(hours=1)}),
        ]
        session = make_session(activity)
        cases.append((Session.objects.filter(pk=session.pk), {'end_at': session.start_at}))
        batch = CertificateBatch.objects.create(title='X', start_date=date(2026, 1, 2), end_date=date(2026, 1, 2),
                                                issue_date=date(2026, 1, 2), created_by=activity.organizer)  # fmt: skip
        cases.append((CertificateBatch.objects.filter(pk=batch.pk), {'end_date': date(2026, 1, 1)}))
        cases.append((CertificateBatch.objects.filter(pk=batch.pk), {'issue_date': date(2026, 1, 1)}))
        for queryset, values in cases:
            with self.subTest(values=values), self.assertRaises(IntegrityError), transaction.atomic():
                queryset.update(**values)


class MaintenanceTests(TestCase):
    def test_faxina(self):
        user = make_user('ana')
        old = timezone.now() - timedelta(days=10)
        code = EmailCode.objects.create(user=user, email=user.email, code_hash='x')
        EmailCode.objects.filter(pk=code.pk).update(created_at=old)
        EmailCode.objects.create(user=user, email=user.email, code_hash='y')
        Throttle.objects.create(key='a' * 64, window_start=old, count=1)
        Throttle.objects.create(key='b' * 64, window_start=timezone.now(), count=1)
        expired = SessionStore()
        expired.create()
        DjangoSession.objects.filter(session_key=expired.session_key).update(expire_date=old)
        alive = SessionStore()
        alive.create()
        with TemporaryDirectory() as private, override_settings(PRIVATE_MEDIA_ROOT=private):
            cache = Path(private) / 'cache' / 'pdf'
            cache.mkdir(parents=True)
            (cache / 'antiga.jpg').write_bytes(b'x')
            (cache / 'atual.jpg').write_bytes(b'x')
            stale = time.time() - 40 * 86400
            os.utime(cache / 'antiga.jpg', (stale, stale))
            results = maintenance.run()
            self.assertEqual(sorted(p.name for p in cache.iterdir()), ['atual.jpg'])
        self.assertEqual(results['Cópias de imagens sem uso apagadas'], 1)
        self.assertEqual(EmailCode.objects.count(), 1)
        self.assertEqual(Throttle.objects.count(), 1)
        self.assertEqual(list(DjangoSession.objects.values_list('session_key', flat=True)), [alive.session_key])

    def test_comando(self):
        out = StringIO()
        from django.core.management import call_command

        call_command('run_maintenance', stdout=out)
        self.assertIn('Sessões vencidas apagadas: 0', out.getvalue())

    def test_falha_de_uma_tarefa_nao_para_as_outras(self):
        with mock.patch.object(maintenance, 'TASKS', [('quebra', lambda: 1 / 0), ('ok', lambda: 3)]):
            results = maintenance.run()
        self.assertTrue(results['quebra'].startswith('falhou'))
        self.assertEqual(results['ok'], 3)


class WorkerScheduleTests(TestCase):
    def run_worker(self, *args):
        from core.management.commands.process_outbox import Command

        calls = []
        command = Command(stdout=StringIO(), stderr=StringIO())
        for name in ('upkeep', 'reminders', 'maintenance'):
            setattr(command, name, lambda name=name: calls.append(name))
        module = 'core.management.commands.process_outbox'
        with (
            mock.patch(f'{module}.time.sleep', side_effect=KeyboardInterrupt),
            mock.patch(f'{module}.close_old_connections'),
        ):
            with self.assertRaises(KeyboardInterrupt):
                command.handle(continuo=True, intervalo=15, sem_lembretes='--sem-lembretes' in args)
        return calls

    def test_worker_roda_lembretes_e_faxina(self):
        from core.models import PeriodicTask

        self.assertEqual(sorted(self.run_worker()), ['maintenance', 'reminders', 'upkeep'])
        # Outro worker logo em seguida não repete: a vez de cada rotina fica registrada no banco.
        self.assertEqual(self.run_worker(), [])
        PeriodicTask.objects.all().delete()
        self.assertEqual(sorted(self.run_worker('--sem-lembretes')), ['maintenance', 'upkeep'])

    def test_lembretes_pelo_worker(self):
        from core.management.commands.process_outbox import Command

        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, start_date=now() + timedelta(hours=10), end_date=now() + timedelta(hours=12),
                                 registration_end=now() + timedelta(hours=9))  # fmt: skip
        from registrations.models import Registration

        registration = Registration.objects.create(user=make_user('ana'), activity=activity)
        Command(stdout=StringIO(), stderr=StringIO()).reminders()
        registration.refresh_from_db()
        self.assertIsNotNone(registration.reminder_sent_at)


@override_settings(ALLOWED_HOSTS=['eventos.exemplo.br'], SECURE_SSL_REDIRECT=True)
class HealthTests(TestCase):
    def test_saude_sem_checagem_de_host_nem_https(self):
        response = self.client.get('/saude/', HTTP_HOST='127.0.0.1:8000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'situacao': 'ok', 'banco': 'ok'})
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_banco_fora(self):
        with mock.patch('django.db.backends.utils.CursorWrapper.execute', side_effect=Exception('down')):
            response = self.client.get('/saude/', HTTP_HOST='127.0.0.1')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['banco'], 'erro')

    def test_outras_rotas_continuam_protegidas(self):
        self.assertEqual(self.client.get('/', HTTP_HOST='127.0.0.1').status_code, 400)
