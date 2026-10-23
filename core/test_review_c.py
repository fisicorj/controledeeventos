"""Itens C da revisão: alerta de erro, cache das configurações, prévia em arquivo, hora da leitura offline,
limite da imagem de capa e prazo para confirmar a vaga da lista de espera."""

import io
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import User
from activities.test_utils import local, make_activity, make_session, make_user, now
from attendance.models import Attendance
from certificates.test_batch import STUDENTS, xlsx
from registrations.models import Registration

from .alerts import ErrorAlertHandler
from .models import EmailSettings, OutgoingEmail


class ErrorAlertTests(TestCase):
    def record(self):
        try:
            raise ValueError('quebrou')
        except ValueError:
            return logging.LogRecord('django.request', logging.ERROR, __file__, 1, 'Internal Server Error', (),
                                     sys.exc_info())  # fmt: skip

    def test_sem_destinatario_nao_avisa(self):
        ErrorAlertHandler().emit(self.record())
        self.assertFalse(OutgoingEmail.objects.exists())

    def test_avisa_com_limite(self):
        EmailSettings.objects.update_or_create(pk=1, defaults={'error_alert_email': 'ti@fsa.br'})
        handler = ErrorAlertHandler()
        for _ in range(12):
            handler.emit(self.record())
        alerts = OutgoingEmail.objects.filter(group='error-alert')
        self.assertEqual(alerts.count(), 10)
        self.assertIn('ValueError: quebrou', alerts.first().body)
        self.assertEqual(alerts.first().to, 'ti@fsa.br')


class RequestCacheTests(TestCase):
    def test_configuracoes_lidas_uma_vez_por_pagina(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org)
        ana = make_user('ana')
        Registration.objects.create(user=ana, activity=activity)
        self.client.force_login(ana)
        self.client.get(reverse('my_registrations'))  # cria o registro das configurações
        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse('my_registrations'))
        reads = [q for q in queries.captured_queries if 'core_sitesettings' in q['sql']]
        self.assertEqual(len(reads), 1)

    def test_formulario_invalido_nao_contamina_a_pagina(self):
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        response = self.client.post(reverse('site_settings'), {'portal_name': 'Nome Novo', 'primary_color': 'xx'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<title>Configurações | Nome Novo')


class PreviewFileTests(TestCase):
    def setUp(self):
        self.private = tempfile.mkdtemp()
        override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.private))
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.private, True)
        self.client.force_login(make_user('org', User.Role.ORGANIZER))

    def test_linhas_em_arquivo_e_nao_na_sessao(self):
        data = {'title': 'Palestra', 'activity_type': 'EVENT', 'start_date': '2026-09-01', 'end_date': '2026-09-01',
                'issue_date': '2026-09-01', 'fix_case': 'on', 'spreadsheet': xlsx(STUDENTS)}  # fmt: skip
        self.client.post(reverse('batch_new'), data)
        stored = self.client.session['certificate_import']
        self.assertNotIn('rows', stored)
        preview = Path(self.private) / 'tmp' / 'importacoes' / f'{stored["token"]}.json'
        self.assertTrue(preview.exists())
        self.assertContains(self.client.get(reverse('batch_preview')), 'Palestra')
        self.client.post(reverse('batch_preview'), {'token': stored['token']})
        self.assertFalse(preview.exists())  # emitido: o arquivo temporário some

    def test_arquivo_sumiu(self):
        session = self.client.session
        session['certificate_import'] = {'token': 'abc', 'batch_id': None, 'event': None, 'columns': []}
        session.save()
        response = self.client.get(reverse('batch_preview'), follow=True)
        self.assertContains(response, 'Envie a planilha para ver a prévia')


class OfflineScanTimeTests(TestCase):
    def test_guarda_a_hora_da_leitura(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, start_date=now() - timedelta(hours=3))
        session = make_session(activity, start_at=now() - timedelta(hours=3), end_at=now() - timedelta(hours=1))
        registration = Registration.objects.create(user=make_user('ana'), activity=activity)
        self.client.force_login(org)
        scanned = timezone.localtime(now() - timedelta(hours=2))
        response = self.client.post(
            reverse('qr_checkin', args=[session.id]),
            json.dumps({'token': str(registration.qr_token), 'offline': True, 'scanned_at': scanned.isoformat()}),
            content_type='application/json',
        )
        self.assertTrue(response.json()['ok'])
        self.assertEqual(Attendance.objects.get().check_in, scanned)


class CoverImageLimitTests(TestCase):
    def test_capa_ate_5_mb(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org)
        self.client.force_login(org)
        buffer = io.BytesIO()
        # PNG de ruído (não comprime): ~5,4 MB. Só JPG, PNG e WebP são aceitos (core/images.py).
        Image.frombytes('RGB', (1500, 1200), os.urandom(1500 * 1200 * 3)).save(buffer, 'PNG')
        a = activity
        data = {'title': a.title, 'description': a.description, 'activity_type': a.activity_type,
                'start_date': local(a.start_date), 'end_date': local(a.end_date),
                'registration_start': local(a.registration_start), 'registration_end': local(a.registration_end),
                'capacity': a.capacity, 'status': a.status, 'min_attendance_percent': 75,
                'image': SimpleUploadedFile('capa.png', buffer.getvalue(), 'image/png')}  # fmt: skip
        response = self.client.post(reverse('activity_edit', args=[activity.id]), data)
        self.assertContains(response, 'no máximo 5 MB')


class WaitlistOfferTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        start = now() + timedelta(days=5)
        self.activity = make_activity(self.org, capacity=1, start_date=start, end_date=start + timedelta(hours=4))
        self.ana, self.bia, self.caio = make_user('ana'), make_user('bia'), make_user('caio')
        self.reg_ana = Registration.objects.create(user=self.ana, activity=self.activity)
        self.reg_bia = Registration.objects.create(
            user=self.bia, activity=self.activity, status='WAITLISTED', waitlisted_at=now() - timedelta(hours=2)
        )
        self.reg_caio = Registration.objects.create(
            user=self.caio, activity=self.activity, status='WAITLISTED', waitlisted_at=now() - timedelta(hours=1)
        )

    def cancel_ana(self):
        self.client.force_login(self.ana)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cancel_registration', args=[self.reg_ana.pk]))

    def test_vaga_reservada_e_confirmada(self):
        self.cancel_ana()
        self.reg_bia.refresh_from_db()
        self.assertTrue(self.reg_bia.offer_pending)
        self.assertAlmostEqual((self.reg_bia.offer_expires_at - timezone.now()).total_seconds(), 24 * 3600, delta=120)
        offer = next(m for m in mail.outbox if m.to == ['bia@exemplo.com'])
        self.assertIn('confirme até', offer.subject)
        self.assertEqual(offer.attachments, [])  # o QR só vai depois de confirmar
        self.client.force_login(self.bia)
        self.assertContains(self.client.get(reverse('my_registrations')), 'Confirmar minha vaga')
        self.assertEqual(self.client.get(reverse('registration_qr', args=[self.reg_bia.pk])).status_code, 404)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('confirm_offer', args=[self.reg_bia.pk]), follow=True)
        self.assertContains(response, 'Vaga confirmada')
        self.reg_bia.refresh_from_db()
        self.assertFalse(self.reg_bia.offer_pending)
        self.assertEqual(mail.outbox[0].attachments[0][0], 'qrcode-inscricao.png')
        self.assertEqual(self.client.get(reverse('registration_qr', args=[self.reg_bia.pk])).status_code, 200)

    def test_prazo_vencido_passa_para_o_proximo(self):
        self.cancel_ana()
        Registration.objects.filter(pk=self.reg_bia.pk).update(offer_expires_at=timezone.now() - timedelta(minutes=1))
        mail.outbox.clear()
        out = io.StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command('expire_waitlist_offers', stdout=out)
        self.assertIn('devolvidas à fila: 1', out.getvalue())
        self.reg_bia.refresh_from_db()
        self.reg_caio.refresh_from_db()
        self.assertEqual(self.reg_bia.status, 'CANCELLED')
        self.assertTrue(self.reg_caio.offer_pending)
        subjects = {m.to[0]: m.subject for m in mail.outbox}
        self.assertIn('terminou', subjects['bia@exemplo.com'])
        self.assertIn('confirme até', subjects['caio@exemplo.com'])
        # confirmar depois do prazo não vale
        self.client.force_login(self.bia)
        self.client.post(reverse('confirm_offer', args=[self.reg_bia.pk]))
        self.reg_bia.refresh_from_db()
        self.assertEqual(self.reg_bia.status, 'CANCELLED')

    def test_confirmacao_direta_perto_do_inicio_ou_sem_prazo(self):
        self.reg_caio.delete()
        for hours, start in ((24, now() + timedelta(hours=10)), (0, now() + timedelta(days=5))):
            Registration.objects.filter(pk__in=[self.reg_ana.pk, self.reg_bia.pk]).update(
                status='CONFIRMED', offer_expires_at=None
            )
            Registration.objects.filter(pk=self.reg_bia.pk).update(status='WAITLISTED', waitlisted_at=now())
            self.activity.refresh_from_db()
            self.activity.waitlist_offer_hours, self.activity.start_date = hours, start
            self.activity.end_date = start + timedelta(hours=4)
            self.activity.registration_end = min(self.activity.registration_end, start)
            self.activity.save()
            self.cancel_ana()
            self.reg_bia.refresh_from_db()
            self.assertEqual((self.reg_bia.status, self.reg_bia.offer_expires_at), ('CONFIRMED', None))
