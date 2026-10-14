"""Vínculo de certificado avulso pelo código (Minhas horas)."""

from datetime import date

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.privacy import anonymize
from activities.test_utils import make_user

from .claim import names_match
from .models import Certificate, CertificateBatch, CertificateEvent


class NamesMatchTests(TestCase):
    def test_primeiro_e_ultimo_nome(self):
        ana = User(first_name='Ana', last_name='Lima')
        self.assertTrue(names_match(ana, 'ANA DE SOUZA LIMA'))
        self.assertTrue(names_match(User(first_name='Ângela', last_name='Conceição'), 'angela da conceicao'))
        self.assertFalse(names_match(ana, 'Ana Souza'))
        self.assertFalse(names_match(User(first_name='Ana', last_name=''), 'Ana Lima'))
        self.assertFalse(names_match(User(first_name='', last_name=''), ''))


class ClaimTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.ana = make_user('ana', first_name='Ana', last_name='Lima')
        self.batch = CertificateBatch.objects.create(
            title='Semana', start_date=date(2026, 3, 1), end_date=date(2026, 3, 1), issue_date=date(2026, 3, 1),
            created_by=self.org,
        )  # fmt: skip
        self.cert = self.make('Ana de Souza Lima', 'ana.antigo@exemplo.com')
        self.client.force_login(self.ana)

    def make(self, name, email=''):
        moment = timezone.now()
        return Certificate.objects.create(
            batch=self.batch, participant_name=name, participant_email=email, activity_title='Semana',
            activity_type='Evento', start_date=moment, end_date=moment, workload_hours=10,
        )  # fmt: skip

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_vincula_confere_e_desfaz(self):
        hours = reverse('my_hours')
        self.assertNotContains(self.client.get(hours), self.cert.code)
        typed = self.cert.code.replace('-', '').lower()  # sem traços e em minúsculas também vale
        start = self.client.post(reverse('certificate_claim_start'), {'codigo': typed})
        confirm = reverse('certificate_claim', args=[self.cert.code])
        self.assertRedirects(start, confirm)
        page = self.client.get(confirm)
        self.assertContains(page, 'Ana de Souza Lima')
        self.cert.refresh_from_db()
        self.assertIsNone(self.cert.claimed_by)  # só olhar não vincula
        self.assertRedirects(self.client.post(confirm), hours)
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.claimed_by, self.ana)
        page = self.client.get(hours)
        self.assertContains(page, self.cert.code)
        self.assertContains(page, 'vinculado por você')
        self.assertEqual(page.context['total'], 10)
        self.assertEqual(self.client.get(reverse('certificate_download', args=[self.cert.code])).status_code, 200)
        event = CertificateEvent.objects.get(action='CLAIMED')
        self.assertEqual((event.actor, event.details['account']), (self.ana, 'ana'))
        self.client.post(reverse('certificate_claim_undo', args=[self.cert.code]))
        self.cert.refresh_from_db()
        self.assertIsNone(self.cert.claimed_by)
        self.assertTrue(CertificateEvent.objects.filter(action='UNCLAIMED').exists())

    def test_recusas(self):
        other_name = self.make('Bruna Lima')
        taken = self.make('Ana Lima')
        Certificate.objects.filter(pk=taken.pk).update(claimed_by=self.org)
        revoked = self.make('Ana Lima')
        Certificate.objects.filter(pk=revoked.pk).update(revoked_at=timezone.now())
        cases = {
            other_name.code: 'não confere com o nome da sua conta',
            taken.code: 'já foi vinculado a outra conta',
            revoked.code: 'revogado',
            'ZZZZ-ZZZZ-ZZZZ': 'Não encontramos',
        }
        for code, expected in cases.items():
            response = self.client.post(reverse('certificate_claim', args=[code]))
            self.assertRedirects(response, reverse('my_hours'))
            self.assertTrue(any(expected in m for m in self.messages(response)), (code, self.messages(response)))
        self.assertEqual(Certificate.objects.filter(claimed_by=self.ana).count(), 0)
        bad = self.client.post(reverse('certificate_claim_start'), {'codigo': '123'})
        self.assertTrue(any('Código inválido' in m for m in self.messages(bad)))

    def test_proprio_por_email_nao_precisa(self):
        mine = self.make('Ana Lima', self.ana.email)
        response = self.client.get(reverse('certificate_claim', args=[mine.code]))
        self.assertTrue(any('já aparece nas suas horas' in m for m in self.messages(response)))

    def test_limite_de_tentativas(self):
        for _ in range(10):
            self.client.get(reverse('certificate_claim', args=['ZZZZ-ZZZZ-ZZZZ']))
        response = self.client.get(reverse('certificate_claim', args=[self.cert.code]))
        self.assertTrue(any('Muitas tentativas' in m for m in self.messages(response)))

    def test_outra_pessoa_nao_desfaz_e_exclusao_de_conta_solta(self):
        self.client.post(reverse('certificate_claim', args=[self.cert.code]))
        self.client.force_login(make_user('bia', first_name='Bia', last_name='Reis'))
        self.assertEqual(self.client.post(reverse('certificate_claim_undo', args=[self.cert.code])).status_code, 404)
        anonymize(self.ana)
        self.cert.refresh_from_db()
        self.assertIsNone(self.cert.claimed_by)
