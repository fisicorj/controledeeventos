import csv
import io

from django.contrib import admin
from django.core import mail
from django.db import DatabaseError, transaction
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activities.test_utils import make_activity, make_session, make_user
from attendance.models import Attendance
from core.models import EmailSettings
from registrations.models import Registration

from .models import Certificate, CertificateBatch, CertificateEvent
from .test_batch import STUDENTS, xlsx

Action = CertificateEvent.Action


class BatchAuditTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER, first_name='Manoel', last_name='Prof')
        self.client.force_login(self.org)
        rows = [*STUDENTS[:2], ('Bia Souza', '777', 'EC.1A')]
        self.client.post(
            reverse('batch_new'),
            {'title': 'Semana de Engenharia', 'activity_type': 'EVENT', 'start_date': '2026-08-10',
             'end_date': '2026-08-14', 'workload_hours': '12', 'issue_date': '2026-08-14', 'fix_case': 'on',
             'spreadsheet': xlsx([*rows[:1], *rows[1:]])},
            REMOTE_ADDR='10.1.2.3',
        )  # fmt: skip
        token = self.client.session['certificate_import']['token']
        self.client.post(reverse('batch_preview'), {'token': token}, REMOTE_ADDR='10.1.2.3')
        self.batch = CertificateBatch.objects.get()
        self.alex = self.batch.certificates.get(participant_document='12546456')

    def events(self, action):
        return CertificateEvent.objects.filter(action=action)

    def test_emissao_registrada(self):
        issued = self.events(Action.ISSUED)
        self.assertEqual(issued.count(), 2)
        event = issued.get(code=self.alex.code)
        self.assertEqual(event.actor, self.org)
        self.assertEqual(event.actor_name, 'Manoel Prof (org)')
        self.assertEqual(event.ip, '10.1.2.3')
        self.assertEqual(event.batch, self.batch)
        self.assertEqual(
            (event.participant_name, event.participant_document, event.activity_title),
            ('Alex Silva', '12546456', 'Semana de Engenharia'),
        )
        self.assertEqual(event.details['source'], 'Emissão avulsa')

    def test_alteracao_guarda_antes_e_depois(self):
        self.client.post(
            reverse('batch_edit', args=[self.batch.pk]),
            {'title': 'Semana da Engenharia', 'activity_type': 'EVENT', 'start_date': '2026-08-10',
             'end_date': '2026-08-14', 'workload_hours': '16', 'issue_date': '2026-08-14'},
        )  # fmt: skip
        updated = self.events(Action.UPDATED).get(code=self.alex.code)
        self.assertEqual(
            updated.details['changes'],
            {'activity_title': ['Semana de Engenharia', 'Semana da Engenharia'], 'workload_hours': ['12', '16']},
        )
        self.assertIn('Carga horária: 12 → 16', updated.summary)
        # salvar de novo sem mudar nada não gera evento
        self.client.post(
            reverse('batch_edit', args=[self.batch.pk]),
            {'title': 'Semana da Engenharia', 'activity_type': 'EVENT', 'start_date': '2026-08-10',
             'end_date': '2026-08-14', 'workload_hours': '16', 'issue_date': '2026-08-14'},
        )  # fmt: skip
        self.assertEqual(self.events(Action.UPDATED).count(), 2)

    def test_revogacao_cancelamento_e_downloads(self):
        self.client.post(
            reverse('batch_certificate_revoke', args=[self.batch.pk, self.alex.code]), {'reason': 'Erro no nome'}
        )
        revoked = self.events(Action.REVOKED).get()
        self.assertEqual((revoked.code, revoked.details['reason']), (self.alex.code, 'Erro no nome'))

        self.client.get(reverse('batch_zip', args=[self.batch.pk]))
        self.assertEqual(self.events(Action.DOWNLOADED).filter(details__via='zip').count(), 1)  # só o válido
        other = self.batch.certificates.exclude(pk=self.alex.pk).get()
        self.client.get(reverse('certificate_download', args=[other.code]))
        self.assertEqual(self.events(Action.DOWNLOADED).filter(details__via='pdf').count(), 1)

        self.client.post(reverse('batch_cancel', args=[self.batch.pk]), {'reason': 'Duplicado'})
        cancel = self.events(Action.REVOKED).filter(details__batch_cancelled=True)
        self.assertEqual(list(cancel.values_list('code', flat=True)), [other.code])  # o já revogado não repete

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_envio_por_email_registrado(self):
        Certificate.objects.filter(pk=self.alex.pk).update(participant_email='alex@exemplo.com')
        config = EmailSettings.load()
        config.enabled = True
        config.save()
        self.client.post(reverse('batch_email', args=[self.batch.pk]))
        self.assertEqual(len(mail.outbox), 1)
        event = self.events(Action.EMAILED).get()
        self.assertEqual((event.code, event.details['recipient']), (self.alex.code, 'alex@exemplo.com'))

    def test_registro_nao_pode_ser_alterado_nem_apagado(self):
        event = CertificateEvent.objects.first()
        with self.assertRaises(RuntimeError):
            event.save()
        with self.assertRaises(RuntimeError):
            event.delete()
        # nem por fora do Django: o banco recusa
        for operation in (
            lambda: CertificateEvent.objects.filter(pk=event.pk).update(participant_name='Outro'),
            lambda: CertificateEvent.objects.filter(pk=event.pk)._raw_delete(CertificateEvent.objects.db),
        ):
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()
        # certificado com histórico não pode ser apagado
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.alex.delete()
        self.assertEqual(Certificate.objects.count(), 2)

    def test_admin_do_django_so_consulta(self):
        request = RequestFactory().get('/')
        request.user = make_user('root', User.Role.ADMIN, is_superuser=True, is_staff=True)
        for model in (Certificate, CertificateBatch, CertificateEvent):
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_delete_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_add_permission(request))

    def test_tela_de_auditoria(self):
        url = reverse('certificate_audit')
        self.assertEqual(self.client.get(url).status_code, 403)  # organizador não vê
        self.client.post(
            reverse('batch_certificate_revoke', args=[self.batch.pk, self.alex.code]), {'reason': 'Erro no nome'}
        )
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        page = self.client.get(url)
        self.assertContains(page, '3 registros')
        self.assertContains(page, 'Motivo: Erro no nome')
        self.assertContains(self.client.get(url, {'acao': 'REVOKED'}), '1 registro<')
        self.assertContains(self.client.get(url, {'q': '12546456'}), '2 registros')
        self.assertContains(self.client.get(url, {'q': self.alex.code.lower()}), '2 registros')
        self.assertContains(self.client.get(url, {'usuario': 'manoel'}), '3 registros')
        self.assertContains(self.client.get(url, {'lote': self.batch.pk}), 'Mostrando só a emissão avulsa')
        self.assertContains(self.client.get(url, {'de': '2000-01-01', 'ate': '2000-01-02'}), '0 registros')

        response = self.client.get(reverse('certificate_audit_csv'), {'acao': 'REVOKED'})
        lines = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(lines[0][:3], ['Data e hora', 'Ação', 'Código'])
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1][1:4], ['Revogado', self.alex.code, 'Alex Silva'])
        self.assertEqual(lines[1][7], 'Manoel Prof (org)')
        self.assertEqual(lines[1][9], 'Motivo: Erro no nome')

    def test_historico_na_pagina_do_lote(self):
        detail = self.client.get(reverse('batch_detail', args=[self.batch.pk]))
        self.assertContains(detail, 'Histórico')
        self.assertContains(detail, '>Emitido</span>', count=2)


class ActivityAuditTests(TestCase):
    def test_finalizar_atividade_registra_emissao(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, certificate_enabled=True, min_attendance_percent=50)
        session = make_session(activity)
        ana = make_user('ana', first_name='Ana', last_name='Souza')
        registration = Registration.objects.create(user=ana, activity=activity)
        Attendance.objects.create(registration=registration, session=session, validated_by=org)
        self.client.force_login(org)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('activity_finish', args=[activity.id]))
        certificate = Certificate.objects.get()
        event = CertificateEvent.objects.get(action=Action.ISSUED)
        self.assertEqual((event.code, event.actor, event.batch), (certificate.code, org, None))
        self.assertEqual(event.details['source'], 'Atividade do sistema')

        # o próprio participante baixar não entra na auditoria; a equipe, sim
        self.client.force_login(ana)
        self.client.get(reverse('certificate_download', args=[certificate.code]))
        self.assertFalse(CertificateEvent.objects.filter(action=Action.DOWNLOADED).exists())
        self.client.force_login(org)
        self.client.get(reverse('certificate_download', args=[certificate.code]))
        self.assertEqual(CertificateEvent.objects.filter(action=Action.DOWNLOADED).count(), 1)
