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
        with TemporaryDirectory() as folder, override_settings(AUDIT_EXPORT_DIR=folder):
            response = self.client.post(reverse('maintenance_run'))
            self.assertRedirects(response, self.url)
            self.assertTrue(MaintenanceRun.objects.get().source.startswith('manual (chefe)'))
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
