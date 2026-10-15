"""Configurações > Manutenção: situação da faxina diária e da exportação da auditoria."""

from datetime import timedelta
from io import StringIO
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.test_utils import make_user
from core import maintenance
from core.models import MaintenanceRun


class MaintenancePanelTests(TestCase):
    def setUp(self):
        self.admin = make_user('chefe', User.Role.ADMIN)
        self.client.force_login(self.admin)
        self.url = reverse('maintenance_status')

    def test_so_administrador(self):
        self.client.force_login(make_user('org', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(reverse('maintenance_run')).status_code, 403)

    def test_avisa_quando_nunca_rodou_ou_parou(self):
        self.assertContains(self.client.get(self.url), 'ainda não rodou nenhuma vez')
        call_command('run_maintenance', stdout=StringIO())
        run = MaintenanceRun.objects.get()
        self.assertEqual(run.source, 'comando')
        self.assertFalse(run.failed)
        self.assertIn('Sessões vencidas apagadas', run.results)
        self.assertContains(self.client.get(self.url), 'sem falhas')
        MaintenanceRun.objects.update(started_at=timezone.now() - timedelta(days=2))
        self.assertContains(self.client.get(self.url), 'há mais de um dia e meio')

    def test_falha_de_uma_tarefa_aparece(self):
        def boom():
            raise RuntimeError('disco cheio')

        tasks = [('Teste', boom), *maintenance.TASKS[:1]]
        with self._tasks(tasks):
            maintenance.run()
        run = MaintenanceRun.objects.get()
        self.assertTrue(run.failed)
        page = self.client.get(self.url)
        self.assertContains(page, 'teve falha')
        self.assertContains(page, 'falhou: disco cheio')

    def test_rodar_agora_e_exportacao(self):
        with TemporaryDirectory() as folder, override_settings(AUDIT_EXPORT_DIR=folder, OUTBOX_MODE='sync'):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse('maintenance_run'))
            self.assertRedirects(response, self.url)
            run = MaintenanceRun.objects.get()
            self.assertTrue(run.source.startswith('manual (chefe)'))
            self.assertFalse(run.queued)
            self.assertIsNotNone(run.finished_at)
            page = self.client.get(self.url)
            self.assertContains(page, folder)
            self.assertContains(page, 'Presenças removidas')
        self.assertContains(self.client.get(self.url), 'Desligada')

    def test_guarda_so_as_ultimas(self):
        MaintenanceRun.objects.bulk_create([MaintenanceRun() for _ in range(MaintenanceRun.KEEP + 5)])
        with self._tasks([]):
            maintenance.run()
        self.assertEqual(MaintenanceRun.objects.count(), MaintenanceRun.KEEP)

    def _tasks(self, tasks):
        from unittest import mock

        return mock.patch.object(maintenance, 'TASKS', tasks)

    def test_rodar_agora_vai_para_a_fila_do_worker(self):
        # DES-05 da 3ª auditoria: a faxina não roda dentro da requisição
        from io import StringIO

        from django.core.management import call_command

        with override_settings(OUTBOX_MODE='off'), self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('maintenance_run'))
            self.client.post(reverse('maintenance_run'))  # pedido repetido não duplica
        run = MaintenanceRun.objects.get()
        self.assertTrue(run.queued)
        page = self.client.get(self.url)
        self.assertContains(page, 'Faxina em andamento')
        self.assertContains(page, 'http-equiv="refresh"')
        with self._tasks([]):
            call_command('process_outbox', stdout=StringIO())
        run.refresh_from_db()
        self.assertFalse(run.queued)
        self.assertIsNotNone(run.finished_at)
        self.assertNotContains(self.client.get(self.url), 'Faxina em andamento')

    def test_nome_de_usuario_longo_cabe_na_origem(self):
        long_admin = make_user('a' * 60, User.Role.ADMIN)
        self.client.force_login(long_admin)
        with override_settings(OUTBOX_MODE='off'), self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('maintenance_run'))
        self.assertEqual(MaintenanceRun.objects.get().source, f'manual ({"a" * 60})')

    def test_faxina_interrompida_libera_o_botao(self):
        # DES-06 da 4ª auditoria: o worker caiu no meio; antes, o botão ficava escondido por 36 horas
        from datetime import timedelta

        from django.utils import timezone

        MaintenanceRun.objects.create(source='worker', started_at=timezone.now() - timedelta(hours=2))
        page = self.client.get(self.url)
        self.assertNotContains(page, 'Faxina em andamento')
        self.assertContains(page, 'Rodar agora')
        self.assertNotContains(page, 'http-equiv="refresh"')
        MaintenanceRun.objects.create(source='worker')  # outra em andamento agora
        page = self.client.get(self.url)
        self.assertContains(page, 'Faxina em andamento')
        self.assertContains(page, 'interrompida')
