"""Declaração de horas verificável (P3 da 3ª auditoria)."""

import io
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.test_utils import make_user

from .models import Certificate, CertificateBatch, HoursDeclaration


class DeclarationSetup(TestCase):
    def setUp(self):
        org = make_user('org', User.Role.ORGANIZER)
        self.ana = make_user('ana', first_name='Ana', last_name='Lima', email_verified=True)
        batch = CertificateBatch.objects.create(
            title='Semana', start_date=date(2026, 3, 1), end_date=date(2026, 3, 1), issue_date=date(2026, 3, 1),
            created_by=org,
        )  # fmt: skip
        moment = timezone.now()
        self.certs = [
            Certificate.objects.create(
                batch=batch,
                participant_name='Ana Lima',
                participant_email='ana@exemplo.com',
                activity_title=title,
                activity_type='Evento',
                start_date=moment,
                end_date=moment,
                workload_hours=hours,
                document_date=date(2026, 3, 1),
            )  # fmt: skip
            for title, hours in (('Palestra', 4), ('Oficina', 8))
        ]
        self.client.force_login(self.ana)

    def download(self):
        response = self.client.get(reverse('my_hours') + '?formato=pdf')
        self.assertEqual(response['Content-Type'], 'application/pdf')
        return response, HoursDeclaration.objects.latest('created_at')


class DeclarationVerifyTests(DeclarationSetup):
    def test_pdf_traz_codigo_e_pagina_publica_confere(self):
        response, declaration = self.download()
        self.assertEqual((declaration.total_hours, len(declaration.items)), (12, 2))
        try:
            from pypdf import PdfReader
        except ImportError:
            PdfReader = None
        if PdfReader:
            text = ' '.join(' '.join(p.extract_text() for p in PdfReader(io.BytesIO(response.content)).pages).split())
            self.assertIn(declaration.code, text)
        page = Client().get(reverse('declaration_verify', args=[declaration.code]))
        self.assertContains(page, 'Declaração autêntica')
        self.assertContains(page, 'Ana Lima')
        self.assertContains(page, '12 horas')
        self.assertContains(page, self.certs[0].code)
        self.assertNotContains(page, 'ana@exemplo.com')

    def test_revogacao_posterior_aparece(self):
        _, declaration = self.download()
        Certificate.objects.filter(pk=self.certs[1].pk).update(revoked_at=timezone.now())
        page = Client().get(reverse('declaration_verify', args=[declaration.code]))
        self.assertContains(page, 'com certificado revogado')
        self.assertContains(page, 'revogado</span>')

    def test_codigo_digitado_na_validacao_leva_para_a_declaracao(self):
        _, declaration = self.download()
        response = Client().get(reverse('certificate_verify_code', args=[declaration.code.replace('-', '')]))
        self.assertRedirects(response, reverse('declaration_verify', args=[declaration.code]))
        self.assertEqual(Client().get(reverse('declaration_verify', args=['ZZZZ-ZZZZ-ZZZZ'])).status_code, 404)

    def test_exclusao_da_conta_apaga_as_declaracoes(self):
        from accounts.privacy import anonymize, export_data

        _, declaration = self.download()
        self.assertIn(declaration.code, str(export_data(self.ana)))
        anonymize(self.ana)
        self.assertFalse(HoursDeclaration.objects.exists())
        self.assertEqual(Client().get(reverse('declaration_verify', args=[declaration.code])).status_code, 404)

    def test_mesmo_dia_mesma_lista_repete_o_codigo(self):
        # SEC-28 da 4ª auditoria
        _, first = self.download()
        _, again = self.download()
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(HoursDeclaration.objects.count(), 1)
        Certificate.objects.filter(pk=self.certs[1].pk).update(revoked_at=timezone.now())  # lista mudou
        _, changed = self.download()
        self.assertNotEqual(changed.pk, first.pk)

    def test_limite_diario(self):
        from unittest import mock

        from . import declaration

        with mock.patch.object(declaration, 'DAILY_LIMIT', 1):
            self.download()
            Certificate.objects.filter(pk=self.certs[1].pk).update(revoked_at=timezone.now())
            response = self.client.get(reverse('my_hours') + '?formato=pdf')
        self.assertRedirects(response, reverse('my_hours'))
        self.assertEqual(HoursDeclaration.objects.count(), 1)

    def test_pessoa_ve_e_cancela_as_suas(self):
        _, declaration = self.download()
        page = self.client.get(reverse('my_hours'))
        self.assertContains(page, 'Declarações emitidas')
        self.assertContains(page, declaration.code)
        other = Client()
        other.force_login(make_user('bia'))
        self.assertEqual(other.post(reverse('declaration_cancel', args=[declaration.code])).status_code, 404)
        self.client.post(reverse('declaration_cancel', args=[declaration.code]))
        declaration.refresh_from_db()
        self.assertIsNotNone(declaration.cancelled_at)
        public = Client().get(reverse('declaration_verify', args=[declaration.code]))
        self.assertContains(public, 'Declaração cancelada')
        self.assertNotContains(public, 'Ana Lima')
        self.assertNotContains(public, self.certs[0].code)
        _, new = self.download()  # depois de cancelar, baixar de novo gera outra
        self.assertNotEqual(new.pk, declaration.pk)


class BulkVerifyTests(DeclarationSetup):
    """Verificação em lote (P3 da 4ª auditoria)."""

    def test_varios_codigos_de_uma_vez(self):
        from django.utils import timezone

        _, declaration = self.download()
        Certificate.objects.filter(pk=self.certs[1].pk).update(revoked_at=timezone.now())
        typed = (
            f'{self.certs[0].code}\n{self.certs[1].code.lower().replace("-", "")}, {declaration.code}; ZZZZ-ZZZZ-ZZZZ'
        )
        page = Client().post(reverse('certificate_verify_bulk'), {'codigos': typed})
        rows = page.context['rows']
        self.assertEqual([(r['kind'], r['valid']) for r in rows], [
            ('Certificado', True), ('Certificado', False), ('Declaração de horas', True), (None, False),
        ])  # fmt: skip
        self.assertEqual(page.context['summary'], {'valid': 2, 'invalid': 1, 'missing': 1})
        self.assertContains(page, 'Ana Lima')

    def test_limite_de_codigos_e_de_inexistentes(self):
        from unittest import mock

        from . import views

        too_many = '\n'.join(['ABCD-EFGH-JKMN'] * 51)
        self.assertContains(Client().post(reverse('certificate_verify_bulk'), {'codigos': too_many}), 'no máximo 50')
        with mock.patch.object(views, 'VERIFY_MISSES', 2):
            page = Client().post(
                reverse('certificate_verify_bulk'), {'codigos': 'AAAA-AAAA-AAAA BBBB-BBBB-BBBB ' + self.certs[0].code}
            )
        self.assertTrue(page.context['throttled'])
        self.assertEqual(len(page.context['rows']), 2)  # o terceiro não foi conferido
