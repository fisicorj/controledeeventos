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


class ClaimSetup(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.ana = make_user('ana', first_name='Ana', last_name='Lima')
        self.batch = CertificateBatch.objects.create(
            title='Semana', start_date=date(2026, 3, 1), end_date=date(2026, 3, 1), issue_date=date(2026, 3, 1),
            created_by=self.org,
        )  # fmt: skip
        self.cert = self.make('Ana de Souza Lima', 'ana.antigo@exemplo.com')
        self.client.force_login(self.ana)

    def make(self, name, email='', document=''):
        moment = timezone.now()
        return Certificate.objects.create(
            batch=self.batch, participant_name=name, participant_email=email, activity_title='Semana',
            activity_type='Evento', start_date=moment, end_date=moment, workload_hours=10,
            participant_document=document,
        )  # fmt: skip

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class ClaimTests(ClaimSetup):
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


class ClaimHardeningTests(ClaimSetup):
    """SEC-24 da 3ª auditoria: homônimos, documento da planilha e certificado que já é de outra conta."""

    def claim(self, certificate):
        response = self.client.post(reverse('certificate_claim', args=[certificate.code]))
        certificate.refresh_from_db()
        return certificate.claimed_by, self.messages(response)

    def test_cpf_da_planilha_precisa_conferir(self):
        cert = self.make('Ana Lima', document='123.456.789-09')
        owner, messages = self.claim(cert)
        self.assertIsNone(owner)
        self.assertTrue(any('CPF no certificado não confere' in m for m in messages))
        User.objects.filter(pk=self.ana.pk).update(institutional_id='123.456.789-09')
        self.assertEqual(self.claim(cert)[0], self.ana)

    def test_matricula_precisa_estar_confirmada(self):
        from core.models import SiteSettings

        SiteSettings.objects.update_or_create(pk=1, defaults={'student_email_domains': 'aluno.fsa.br'})
        cert = self.make('Ana Lima', document='2312345')
        User.objects.filter(pk=self.ana.pk).update(institutional_id='2312345')  # digitada, sem confirmar
        owner, messages = self.claim(cert)
        self.assertIsNone(owner)
        self.assertTrue(any('matrícula confirmada' in m for m in messages))
        # com o e-mail institucional confirmado, a matrícula vale e o certificado já entra sozinho nas horas
        User.objects.filter(pk=self.ana.pk).update(email='2312345@aluno.fsa.br')
        owner, messages = self.claim(cert)
        self.assertIsNone(owner)
        self.assertTrue(any('já aparece nas suas horas' in m for m in messages))

    def test_homonimo_nao_leva_o_certificado_de_outra_conta(self):
        other = make_user('outra_ana', first_name='Ana', last_name='Lima', email_verified=True)
        User.objects.filter(pk=other.pk).update(email='ana.lima@exemplo.com')
        cert = self.make('Ana Lima', email='ana.lima@exemplo.com')
        owner, messages = self.claim(cert)
        self.assertIsNone(owner)
        self.assertTrue(any('já pertence a outra conta' in m for m in messages))

    def test_avisa_o_email_da_planilha_e_marca_na_declaracao(self):
        from core.models import OutgoingEmail

        with self.captureOnCommitCallbacks(execute=True):
            self.claim(self.cert)
        notice = OutgoingEmail.objects.get(group='claim')
        self.assertEqual(notice.to, 'ana.antigo@exemplo.com')
        self.assertIn('Ana Lima', notice.body)
        response = self.client.get(reverse('my_hours') + '?formato=pdf')
        try:
            import io

            from pypdf import PdfReader
        except ImportError:
            return
        text = ' '.join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages)
        self.assertIn('vinculado a esta conta pela própria pessoa', ' '.join(text.split()))


class ExportIncludesClaimedTests(ClaimSetup):
    def test_copia_dos_dados_inclui_os_vinculados(self):
        # SEC-26 da 3ª auditoria: a cópia dos dados usa a mesma regra de "Minhas horas"
        from accounts.privacy import export_data

        self.assertNotIn(self.cert.code, str(export_data(self.ana)))
        self.client.post(reverse('certificate_claim', args=[self.cert.code]))
        self.assertIn(self.cert.code, str(export_data(self.ana)))
