"""DES-02: ZIP de lotes grandes montado em segundo plano."""

import io
import zipfile
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.test_utils import make_user

from . import zips
from .audit import Action
from .models import Certificate, CertificateBatch, CertificateEvent, ZipExport


@override_settings(OUTBOX_MODE='sync')
class ZipExportTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.tmp.name))
        override.enable()
        self.addCleanup(override.disable)
        limit = mock.patch.object(zips, 'ZIP_DIRECT_LIMIT', 1)  # dois certificados já contam como lote grande
        limit.start()
        self.addCleanup(limit.stop)
        self.org = make_user('org', User.Role.ORGANIZER)
        self.batch = CertificateBatch.objects.create(
            title='Semana', start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), issue_date=date(2026, 1, 2),
            created_by=self.org,
        )  # fmt: skip
        moment = timezone.now()
        for name in ('Ana Lima', 'Bruno Reis'):
            Certificate.objects.create(
                batch=self.batch, participant_name=name, activity_title='Semana', activity_type='Evento',
                start_date=moment, end_date=moment,
            )  # fmt: skip
        self.client.force_login(self.org)

    def test_lote_grande_vai_para_a_fila_e_sai_pronto(self):
        detail = reverse('batch_detail', args=[self.batch.pk])
        self.assertRedirects(self.client.get(reverse('batch_zip', args=[self.batch.pk])), detail)
        self.assertContains(self.client.get(detail), 'Gerar ZIP de todos')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('batch_zip_request', args=[self.batch.pk]))
        export = ZipExport.objects.get()
        self.assertEqual((export.status, export.count, export.requested_by), ('READY', 2, self.org))
        page = self.client.get(detail)
        file_url = reverse('batch_zip_file', args=[self.batch.pk, export.pk])
        self.assertContains(page, file_url)
        self.assertNotContains(page, 'http-equiv="refresh"')
        response = self.client.get(file_url)
        archive = zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content)))
        self.assertEqual(len(archive.namelist()), 2)
        events = CertificateEvent.objects.filter(action=Action.DOWNLOADED, details__via='zip')
        self.assertEqual({e.actor_id for e in events}, {self.org.pk})

    def test_pedido_repetido_nao_duplica_e_pagina_atualiza(self):
        self.client.post(reverse('batch_zip_request', args=[self.batch.pk]))  # sem on_commit: fica na fila
        self.client.post(reverse('batch_zip_request', args=[self.batch.pk]))
        self.assertEqual(ZipExport.objects.count(), 1)
        page = self.client.get(reverse('batch_detail', args=[self.batch.pk]))
        self.assertContains(page, 'Gerando ZIP')
        self.assertContains(page, 'http-equiv="refresh"')
        self.assertEqual(zips.process(), 1)  # o worker
        self.assertEqual(ZipExport.objects.get().status, 'READY')

    def test_outra_pessoa_nao_baixa(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('batch_zip_request', args=[self.batch.pk]))
        export = ZipExport.objects.get()
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(reverse('batch_zip_file', args=[self.batch.pk, export.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('batch_zip_request', args=[self.batch.pk])).status_code, 403)

    def test_faxina_apaga_vencidos_e_destrava(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('batch_zip_request', args=[self.batch.pk]))
        old = ZipExport.objects.get()
        path = Path(old.file.path)
        self.assertTrue(path.exists())
        ZipExport.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=25))
        stuck = ZipExport.objects.create(
            batch=self.batch, status=ZipExport.Status.RUNNING, started_at=timezone.now() - timedelta(hours=2)
        )
        self.assertEqual(zips.cleanup(), 1)
        self.assertFalse(path.exists())
        self.assertFalse(ZipExport.objects.filter(pk=old.pk).exists())
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, 'PENDING')

    def test_mudanca_no_lote_descarta_o_zip_pronto(self):
        from .batch import revoke

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('batch_zip_request', args=[self.batch.pk]))
        path = Path(ZipExport.objects.get().file.path)
        request = mock.Mock(user=self.org, META={})
        with self.captureOnCommitCallbacks(execute=True):
            revoke([self.batch.certificates.first()], 'Nome errado', request)
        self.assertFalse(ZipExport.objects.exists())
        self.assertFalse(path.exists())

    def test_servico_so_de_zips(self):
        # DES-04 da 3ª auditoria: o worker dos e-mails pode deixar os ZIPs para um serviço próprio
        from io import StringIO

        from django.core.management import call_command

        self.client.post(reverse('batch_zip_request', args=[self.batch.pk]))  # fica na fila
        call_command('process_outbox', sem_zips=True, stdout=StringIO())
        self.assertEqual(ZipExport.objects.get().status, 'PENDING')
        out = StringIO()
        call_command('process_outbox', so_zips=True, stdout=out)
        self.assertEqual(ZipExport.objects.get().status, 'READY')
        self.assertIn('ZIPs de certificados gerados: 1', out.getvalue())

    def test_um_zip_por_volta_no_laco_dos_emails(self):
        for _ in range(2):
            ZipExport.objects.create(batch=self.batch)
        self.assertEqual(zips.process(limit=1), 1)
        self.assertEqual(ZipExport.objects.filter(status='PENDING').count(), 1)

    def test_lote_pequeno_continua_direto(self):
        with mock.patch.object(zips, 'ZIP_DIRECT_LIMIT', 200):
            response = self.client.get(reverse('batch_zip', args=[self.batch.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(self.client.get(reverse('batch_detail', args=[self.batch.pk])), 'Baixar todos (ZIP)')
