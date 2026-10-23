"""Relatório de horas por turma (P3 da 4ª auditoria)."""

from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.test_utils import make_user

from .models import Certificate, CertificateBatch


class GroupReportTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.other = make_user('outro', User.Role.ORGANIZER)
        moment = timezone.now()

        def batch(title, owner, day):
            return CertificateBatch.objects.create(
                title=title, start_date=day, end_date=day, issue_date=day, created_by=owner,
            )  # fmt: skip

        def cert(b, name, doc, group, hours, day):
            return Certificate.objects.create(
                batch=b, participant_name=name, participant_document=doc, participant_group=group,
                activity_title=b.title, activity_type='Evento', start_date=moment, end_date=moment,
                workload_hours=hours, document_date=day,
            )  # fmt: skip

        march, sept = date(2026, 3, 10), date(2026, 9, 10)
        b1, b2 = batch('Semana', self.org, march), batch('Oficina', self.org, sept)
        cert(b1, 'Ana Lima', '111', 'ADS-1', 8, march)
        cert(b2, 'ANA LIMA', '111', 'ADS-1', 4, sept)  # mesma pessoa (pela matrícula)
        cert(b1, 'Bruno Reis', '222', 'ADS-1', 8, march)
        revoked = cert(b2, 'Bruno Reis', '222', 'ADS-1', 20, sept)
        Certificate.objects.filter(pk=revoked.pk).update(revoked_at=timezone.now())
        cert(b1, 'Caio Dias', '333', 'ADS-2', 8, march)
        cert(batch('Alheio', self.other, march), 'Dora', '444', 'ADS-1', 99, march)  # de outro organizador
        self.client.force_login(self.org)

    def test_soma_por_pessoa_sem_revogados_nem_alheios(self):
        page = self.client.get(reverse('batch_group_report'), {'turma': 'ADS-1'})
        self.assertEqual(page.context['groups'], ['ADS-1', 'ADS-2'])
        rows = [(name, doc, hours) for name, doc, _, hours in page.context['rows']]
        self.assertEqual(rows, [('Ana Lima', '111', 12), ('Bruno Reis', '222', 8)])
        self.assertEqual(page.context['total'], 20)
        self.assertNotContains(page, 'Dora')

    def test_semestre_e_csv(self):
        page = self.client.get(reverse('batch_group_report'), {'turma': 'ADS-1', 'semestre': '2026/2'})
        self.assertEqual([(n, h) for n, _, _, h in page.context['rows']], [('Ana Lima', 4)])
        csv = self.client.get(reverse('batch_group_report'), {'turma': 'ADS-1', 'formato': 'csv'})
        content = csv.content.decode('utf-8-sig')
        self.assertIn('Ana Lima;111;2;12;', content)
        self.assertIn('ads-1', csv['Content-Disposition'])

    def test_administrador_ve_tudo_e_participante_nao_entra(self):
        self.client.force_login(make_user('chefe', User.Role.ADMIN))
        page = self.client.get(reverse('batch_group_report'), {'turma': 'ADS-1'})
        self.assertContains(page, 'Dora')
        self.client.force_login(make_user('ana'))
        self.assertEqual(self.client.get(reverse('batch_group_report')).status_code, 403)
