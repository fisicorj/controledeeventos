"""Chamada de trabalhos: envio, avaliação às cegas ou aberta, decisão e inscrição automática do autor."""

import tempfile
from datetime import timedelta
from pathlib import Path

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.models import Activity
from activities.test_utils import local, make_activity, make_session, make_user
from registrations.models import Registration, SessionEnrollment

from .models import CallForPapers, Review, Submission

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF'


def pdf(name='trabalho.pdf'):
    return SimpleUploadedFile(name, PDF, content_type='application/pdf')


class CallSetup(TestCase):
    blind = True

    def setUp(self):
        self.private = tempfile.mkdtemp()
        override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.private))
        override.enable()
        self.addCleanup(override.disable)
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(
            self.org, title='Congresso de TI', activity_type=Activity.Type.EVENT, capacity=1,
            start_date=timezone.now() + timedelta(days=30), end_date=timezone.now() + timedelta(days=32),
        )  # fmt: skip
        now = timezone.now()
        self.call = CallForPapers.objects.create(
            activity=self.activity,
            opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=10),
            instructions='Até 10 páginas.',
            areas='Redes\nInteligência artificial',
            blind_review=self.blind,
            max_authors=3,
        )
        self.author = make_user('autora', first_name='Ana', last_name='Autora')
        self.reviewer = make_user('rev', first_name='Rui', last_name='Revisor')
        self.call.reviewers.add(self.reviewer)

    def submit(self, **extra):
        self.client.force_login(self.author)
        data = {
            'title': 'Redes neurais na sala de aula',
            'area': 'Inteligência artificial',
            'abstract': 'Um resumo.',
            'keywords': 'ia, ensino',
            'coauthors': 'Bruno Coautor (Fatec)',
            'file': pdf('meu-trabalho-ana.pdf'),
            **extra,
        }
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse('submission_create', args=[self.activity.slug]), data, follow=True)

    def assign(self, submission, *reviewers):
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse('submission_manage', args=[submission.pk]),
                {'action': 'assign', 'reviewers': [r.pk for r in reviewers]},
                follow=True,
            )

    def review(self, review, recommendation='ACCEPT'):
        self.client.force_login(review.reviewer)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse('review_form', args=[review.pk]),
                {'recommendation': recommendation, 'score': 4, 'comments_to_author': 'Muito bom.',
                 'comments_to_organizer': 'Segredo da banca.'},
                follow=True,
            )  # fmt: skip

    def decide(self, submission, decision, note=''):
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse('submission_manage', args=[submission.pk]),
                {'action': 'decide', 'decision': decision, 'note': note},
                follow=True,
            )


class SubmissionFlowTests(CallSetup):
    def test_fluxo_completo_com_inscricao_automatica(self):
        response = self.submit()
        self.assertContains(response, 'Trabalho enviado')
        submission = Submission.objects.get()
        self.assertEqual(submission.status, Submission.Status.SUBMITTED)
        self.assertEqual(submission.file_name, 'meu-trabalho-ana.pdf')
        self.assertIn('trabalho recebido', mail.outbox[-1].subject)

        mail.outbox.clear()
        self.assign(submission, self.reviewer)
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.UNDER_REVIEW)
        self.assertEqual(mail.outbox[0].to, ['rev@exemplo.com'])

        mail.outbox.clear()
        self.review(Review.objects.get())
        self.assertEqual(mail.outbox[0].to, ['org@exemplo.com'])  # todas as avaliações entregues

        # Evento lotado: o autor aprovado entra mesmo assim
        Registration.objects.create(user=make_user('outro'), activity=self.activity)
        mail.outbox.clear()
        response = self.decide(submission, 'ACCEPTED', 'Parabéns.')
        self.assertContains(response, 'já está inscrito no evento')
        registration = Registration.objects.get(user=self.author)
        self.assertEqual(registration.status, Registration.Status.CONFIRMED)
        decision = mail.outbox[0]
        self.assertIn('foi aprovado', decision.body)
        self.assertIn('Muito bom.', decision.body)
        self.assertNotIn('Segredo da banca', decision.body)
        self.assertNotIn('Rui', decision.body)
        self.assertEqual(decision.attachments[0][2], 'image/png')  # QR Code da inscrição

        # Aparece na página da chamada
        response = self.client.get(reverse('call_page', args=[self.activity.slug]))
        self.assertContains(response, 'Trabalhos aprovados')
        self.assertContains(response, 'Bruno Coautor')

    def test_correcoes_e_nova_versao(self):
        self.submit()
        submission = Submission.objects.get()
        self.assign(submission, self.reviewer)
        self.review(Review.objects.get(), 'REVISION')
        response = self.decide(submission, 'REVISION')
        self.assertContains(response, 'Explique ao autor')
        self.decide(submission, 'REVISION', 'Revise a seção 2.')
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.REVISION)

        self.client.force_login(self.author)
        page = self.client.get(reverse('submission_detail', args=[submission.pk]))
        self.assertContains(page, 'Revise a seção 2.')
        self.assertContains(page, 'Muito bom.')
        mail.outbox.clear()
        data = {'title': submission.title, 'area': submission.area, 'abstract': 'Resumo novo.', 'coauthors': ''}
        response = self.client.post(reverse('submission_edit', args=[submission.pk]), data, follow=True)
        self.assertContains(response, 'Envie o arquivo da versão corrigida')
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('submission_edit', args=[submission.pk]), {**data, 'file': pdf('v2.pdf')})
        submission.refresh_from_db()
        self.assertEqual((submission.version, submission.status), (2, Submission.Status.UNDER_REVIEW))
        self.assertIsNone(Review.objects.get().submitted_at)  # a avaliação reabre
        self.assertIn('rev@exemplo.com', [m.to[0] for m in mail.outbox])

    def test_nao_aprovado_nao_inscreve(self):
        self.submit()
        self.decide(Submission.objects.get(), 'REJECTED', 'Fora do tema.')
        self.assertFalse(Registration.objects.filter(user=self.author).exists())
        self.assertIn('não foi aprovado', mail.outbox[-1].body)

    def test_regras_do_formulario(self):
        response = self.submit(coauthors='A\nB\nC')
        self.assertContains(response, 'No máximo 3 autores')
        response = self.submit(file=SimpleUploadedFile('x.exe', b'MZ'))
        self.assertContains(response, 'PDF, DOC, DOCX ou ODT')
        response = self.submit(area='Outra')
        self.assertContains(response, 'Faça uma escolha válida')
        self.assertFalse(Submission.objects.exists())

    def test_chamada_fechada_ou_atividade_nao_publicada(self):
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.DRAFT)
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(reverse('call_page', args=[self.activity.slug])).status_code, 404)
        self.client.force_login(self.org)
        self.assertContains(self.client.get(reverse('call_page', args=[self.activity.slug])), 'Pré-visualização')
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.PUBLISHED)
        CallForPapers.objects.filter(pk=self.call.pk).update(closes_at=timezone.now() - timedelta(minutes=1))
        response = self.submit()
        self.assertContains(response, 'não estão abertas')

    def test_autor_edita_ate_comecar_a_avaliacao_e_pode_retirar(self):
        self.submit()
        submission = Submission.objects.get()
        self.client.force_login(self.author)
        self.assertContains(self.client.get(reverse('submission_detail', args=[submission.pk])), 'Editar ou trocar')
        self.assign(submission, self.reviewer)
        self.client.force_login(self.author)
        response = self.client.get(reverse('submission_edit', args=[submission.pk]), follow=True)
        self.assertContains(response, 'não pode mais ser alterado')
        self.client.post(reverse('submission_withdraw', args=[submission.pk]))
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.WITHDRAWN)
        self.assertFalse(Review.objects.exists())

    def test_evento_na_pagina_e_na_gestao(self):
        self.client.force_login(self.author)
        self.assertContains(self.client.get(self.activity.get_absolute_url()), 'Enviar trabalho')
        self.client.force_login(self.org)
        self.assertContains(self.client.get(reverse('activity_manage', args=[self.activity.pk])), 'Trabalhos')
        self.submit()
        self.client.force_login(self.org)
        response = self.client.get(reverse('call_manage', args=[self.activity.pk]))
        self.assertContains(response, 'Redes neurais na sala de aula')
        csv = self.client.get(reverse('submissions_csv', args=[self.activity.pk])).content.decode()
        self.assertIn('autora@exemplo.com', csv)
        response = self.client.post(reverse('activity_delete', args=[self.activity.pk]), follow=True)
        self.assertContains(response, 'não pode ser excluída')


class BlindReviewTests(CallSetup):
    def setUp(self):
        super().setUp()
        self.submit()
        self.submission = Submission.objects.get()
        self.assign(self.submission, self.reviewer)
        self.review_obj = Review.objects.get()

    def test_avaliador_nao_ve_autores_nem_nome_do_arquivo(self):
        self.client.force_login(self.reviewer)
        page = self.client.get(reverse('review_form', args=[self.review_obj.pk]))
        self.assertContains(page, 'avaliação às cegas')
        self.assertNotContains(page, 'Autora')
        self.assertNotContains(page, 'Bruno')
        response = self.client.get(reverse('submission_file', args=[self.submission.pk]))
        self.assertNotIn('ana', response['Content-Disposition'])
        self.assertIn(f'trabalho-{self.submission.pk}-v1.pdf', response['Content-Disposition'])
        # PDF enviado por outra pessoa, aberto no próprio portal: sai com a CSP do portal e sem adivinhação de tipo.
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        if response.has_header('Content-Security-Policy'):  # CSP_MODE=off desliga
            self.assertIn("object-src 'none'", response['Content-Security-Policy'])
            self.assertIn("frame-ancestors 'none'", response['Content-Security-Policy'])

    def test_outros_nao_acessam(self):
        self.client.force_login(make_user('curioso'))
        self.assertEqual(self.client.get(reverse('submission_file', args=[self.submission.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('review_form', args=[self.review_obj.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('submission_detail', args=[self.submission.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('submission_manage', args=[self.submission.pk])).status_code, 403)

    def test_autor_nao_e_designado_para_o_proprio_trabalho(self):
        self.call.reviewers.add(self.author)
        self.assign(self.submission, self.reviewer, self.author)
        self.assertFalse(Review.objects.filter(reviewer=self.author).exists())

    def test_modo_nao_muda_com_avaliadores_designados(self):
        from .forms import CallForm

        data = {
            'opens_at': local(self.call.opens_at), 'closes_at': local(self.call.closes_at),
            'instructions': 'x', 'areas': '', 'blind_review': '', 'max_authors': 3,
            'max_submissions': 5,
        }  # fmt: skip
        form = CallForm(data, instance=self.call)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.save().blind_review)


class OpenReviewTests(CallSetup):
    blind = False

    def test_avaliacao_aberta_mostra_autores(self):
        self.submit()
        submission = Submission.objects.get()
        self.assign(submission, self.reviewer)
        self.client.force_login(self.reviewer)
        page = self.client.get(reverse('review_form', args=[Review.objects.get().pk]))
        self.assertContains(page, 'Ana Autora, Bruno Coautor (Fatec)')
        response = self.client.get(reverse('submission_file', args=[submission.pk]))
        self.assertIn('meu-trabalho-ana.pdf', response['Content-Disposition'])


class OrganizerTests(CallSetup):
    def test_abrir_chamada_pela_gestao(self):
        other = make_activity(self.org, title='Simpósio')
        self.client.force_login(self.org)
        response = self.client.get(reverse('call_manage', args=[other.pk]))
        self.assertRedirects(response, reverse('call_edit', args=[other.pk]))
        data = {
            'opens_at': local(timezone.now()),
            'closes_at': local(other.end_date + timedelta(days=1)),
            'instructions': 'Regras',
            'areas': '',
            'blind_review': 'on',
            'max_authors': 4,
            'max_submissions': 5,
        }
        response = self.client.post(reverse('call_edit', args=[other.pk]), data)
        self.assertContains(response, 'até o término do evento')
        data['closes_at'] = local(other.start_date)
        response = self.client.post(reverse('call_edit', args=[other.pk]), data)
        self.assertRedirects(response, reverse('call_manage', args=[other.pk]))
        self.assertTrue(CallForPapers.objects.get(activity=other).blind_review)

    def test_avaliadores_por_usuario_ou_email(self):
        self.client.force_login(self.org)
        person = make_user('nova', email_verified=True)
        self.client.post(reverse('reviewer_add', args=[self.activity.pk]), {'person': person.email})
        self.assertIn(person, self.call.reviewers.all())
        self.submit()
        self.assign(Submission.objects.get(), person)
        self.client.post(reverse('reviewer_remove', args=[self.activity.pk, person.pk]))
        self.assertNotIn(person, self.call.reviewers.all())
        self.assertFalse(Review.objects.filter(reviewer=person).exists())

    def test_trabalho_na_sessao_confirma_o_autor_na_sessao(self):
        Activity.objects.filter(pk=self.activity.pk).update(per_session=True)
        self.activity.refresh_from_db()
        session = make_session(self.activity, capacity=1)
        full = Registration.objects.create(user=make_user('cheio'), activity=self.activity)
        SessionEnrollment.objects.create(registration=full, session=session)
        self.submit()
        submission = Submission.objects.get()
        self.decide(submission, 'ACCEPTED')
        self.client.post(
            reverse('submission_manage', args=[submission.pk]), {'action': 'session', 'session': session.pk}
        )
        enrollment = SessionEnrollment.objects.get(registration__user=self.author, session=session)
        self.assertEqual(enrollment.status, SessionEnrollment.Status.CONFIRMED)
        self.client.force_login(self.author)
        self.assertContains(self.client.get(reverse('submission_detail', args=[submission.pk])), session.title)

    def test_exclusao_de_conta_retira_trabalhos_pendentes(self):
        from accounts.privacy import anonymize, export_data

        self.submit()
        submission = Submission.objects.get()
        self.assertEqual(export_data(self.author)['trabalhos_enviados'][0]['titulo'], submission.title)
        anonymize(self.author)
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.WITHDRAWN)
        self.assertEqual(submission.file_name, '')


class LimitTests(CallSetup):
    def test_limite_de_trabalhos_por_autor(self):
        CallForPapers.objects.filter(pk=self.call.pk).update(max_submissions=2)
        self.submit()
        self.submit(title='Segundo')
        response = self.submit(title='Terceiro')
        self.assertContains(response, 'o máximo desta chamada')
        self.assertEqual(Submission.objects.count(), 2)
        # Retirar libera uma vaga
        Submission.objects.filter(title='Segundo').update(status=Submission.Status.WITHDRAWN)
        self.submit(title='Terceiro')
        self.assertEqual(Submission.objects.count(), 3)

    def test_limite_de_envios_por_hora(self):
        from . import views

        CallForPapers.objects.filter(pk=self.call.pk).update(max_submissions=50)
        for _ in range(views.UPLOADS_PER_HOUR):
            self.submit()
        response = self.submit()
        self.assertContains(response, 'Muitos envios de arquivo')
        self.assertEqual(Submission.objects.count(), views.UPLOADS_PER_HOUR)

    def test_troca_de_arquivo_apaga_o_anterior(self):
        self.submit()
        submission = Submission.objects.get()
        old = submission.file.name
        self.client.force_login(self.author)
        data = {'title': submission.title, 'area': submission.area, 'abstract': 'x', 'coauthors': ''}
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('submission_edit', args=[submission.pk]), {**data, 'file': pdf('novo.pdf')})
        submission.refresh_from_db()
        self.assertNotEqual(submission.file.name, old)
        self.assertFalse(submission.file.storage.exists(old))
        self.assertTrue(submission.file.storage.exists(submission.file.name))

    def test_arquivo_apagado_responde_404(self):
        self.submit()
        submission = Submission.objects.get()
        submission.file.delete(save=False)
        Submission.objects.filter(pk=submission.pk).update(file='', file_name='')
        self.client.force_login(self.org)
        self.assertEqual(self.client.get(reverse('submission_file', args=[submission.pk])).status_code, 404)
        self.assertContains(self.client.get(reverse('submission_manage', args=[submission.pk])), 'Arquivo removido')


class ReopenAndRulesTests(CallSetup):
    def setUp(self):
        super().setUp()
        self.submit()
        self.submission = Submission.objects.get()

    def reopen(self, note='Clique errado.'):
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse('submission_manage', args=[self.submission.pk]), {'action': 'reopen', 'note': note}, follow=True
            )

    def test_reabrir_decisao_registra_e_avisa(self):
        from .models import SubmissionEvent

        self.decide(self.submission, 'REJECTED', 'Fora do tema.')
        mail.outbox.clear()
        response = self.reopen()
        self.assertContains(response, 'Avaliação reaberta')
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, Submission.Status.SUBMITTED)
        self.assertEqual(
            list(self.submission.events.values_list('action', flat=True)),
            [SubmissionEvent.Action.REOPENED, SubmissionEvent.Action.DECIDED],
        )
        self.assertIn('avaliação reaberta', mail.outbox[0].subject)
        self.assertIn('Clique errado.', mail.outbox[0].body)
        # Agora dá para decidir de novo
        self.decide(self.submission, 'ACCEPTED')
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, Submission.Status.ACCEPTED)
        page = self.client.get(reverse('submission_manage', args=[self.submission.pk]))
        self.assertContains(page, 'Histórico')

    def test_reabrir_exige_motivo_e_nao_vale_com_certificado(self):
        from certificates.models import Certificate

        self.decide(self.submission, 'ACCEPTED')
        response = self.reopen(note='')
        self.assertContains(response, 'Este campo é obrigatório')
        Certificate.objects.create(
            submission=self.submission, role='PRESENTER', participant_name='Ana', activity_title='x',
            activity_type='Evento', start_date=timezone.now(), end_date=timezone.now(),
        )  # fmt: skip
        response = self.reopen()
        self.assertContains(response, 'já tem certificado de apresentação')

    def test_evento_cancelado_nao_recebe_decisao(self):
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.CANCELLED)
        response = self.decide(self.submission, 'ACCEPTED')
        self.assertContains(response, 'O evento foi cancelado')
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, Submission.Status.SUBMITTED)
        self.assertFalse(Registration.objects.filter(user=self.author).exists())

    def test_evento_finalizado_aprova_sem_inscrever(self):
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.FINISHED)
        response = self.decide(self.submission, 'ACCEPTED')
        self.assertContains(response, 'não houve inscrição a fazer')
        self.assertFalse(Registration.objects.filter(user=self.author).exists())

    def test_aviso_de_conflito_de_interesse(self):
        bruno = make_user('bruno', first_name='Bruno', last_name='Coautor')
        self.call.reviewers.add(bruno)
        response = self.assign(self.submission, bruno)
        self.assertContains(response, 'aparece entre os coautores')

    def test_avaliador_removido_perde_o_acesso(self):
        self.assign(self.submission, self.reviewer)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(reverse('submission_file', args=[self.submission.pk])).status_code, 200)
        self.call.reviewers.remove(self.reviewer)
        self.assertEqual(self.client.get(reverse('submission_file', args=[self.submission.pk])).status_code, 404)


def _docx(author):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr(
            'docProps/core.xml',
            f'<cp:coreProperties><dc:creator>{author}</dc:creator><cp:lastModifiedBy>{author}</cp:lastModifiedBy>'
            '</cp:coreProperties>',
        )
        z.writestr('word/document.xml', f'<w:document><w:ins w:author="{author}"/>Texto</w:document>')
    return buffer.getvalue()


def _pdf_with_author(author):
    import io

    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.setAuthor(author)
    page.drawString(100, 100, 'Trabalho')
    page.save()
    return buffer.getvalue()


class MetadataTests(CallSetup):
    def stored(self):
        submission = Submission.objects.get()
        with submission.file.open('rb') as handle:
            return handle.read()

    def test_pdf_perde_o_autor_na_avaliacao_as_cegas(self):
        import io

        from pypdf import PdfReader

        self.submit(file=SimpleUploadedFile('t.pdf', _pdf_with_author('Ana Autora')))
        data = self.stored()
        self.assertNotIn(b'Ana Autora', data)
        self.assertIsNone(PdfReader(io.BytesIO(data)).metadata)
        self.assertEqual(Submission.objects.get().file_name, 't.pdf')

    def test_docx_perde_autor_e_revisoes(self):
        import io
        import zipfile

        self.submit(file=SimpleUploadedFile('t.docx', _docx('Ana Autora')))
        data = self.stored()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertNotIn(b'Ana Autora', z.read('docProps/core.xml'))
            self.assertNotIn(b'Ana Autora', z.read('word/document.xml'))
            self.assertIn(b'Texto', z.read('word/document.xml'))

    def test_arquivo_que_nao_abre_fica_como_veio(self):
        self.submit(file=SimpleUploadedFile('t.pdf', b'isto nao e pdf'))
        self.assertEqual(self.stored(), b'isto nao e pdf')


class OpenMetadataTests(CallSetup):
    blind = False

    def test_avaliacao_aberta_nao_mexe_no_arquivo(self):
        original = _pdf_with_author('Ana Autora')
        self.submit(file=SimpleUploadedFile('t.pdf', original))
        with Submission.objects.get().file.open('rb') as handle:
            self.assertEqual(handle.read(), original)


def _zip_bomb(size=70 * 1024 * 1024):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', b'0' * size)
    return buffer.getvalue()


class ArchiveLimitTests(CallSetup):
    def test_docx_que_descompacta_demais_e_recusado(self):
        data = _zip_bomb()
        self.assertLess(len(data), 1024 * 1024)  # menos de 1 MB enviado
        response = self.submit(file=SimpleUploadedFile('t.docx', data))
        self.assertContains(response, 'grande demais depois de descompactado')
        self.assertFalse(Submission.objects.exists())

    def test_parte_grande_demais_e_recusada(self):
        response = self.submit(file=SimpleUploadedFile('t.odt', _zip_bomb(30 * 1024 * 1024)))
        self.assertContains(response, 'grande demais depois de descompactado')

    def test_limpeza_que_passa_do_tempo_mantem_o_arquivo(self):
        from unittest import mock

        from . import metadata

        original = _pdf_with_author('Ana Autora')
        with mock.patch.object(metadata, 'WORKER_TIMEOUT', 0.001):
            self.submit(file=SimpleUploadedFile('t.pdf', original))
        with Submission.objects.get().file.open('rb') as handle:
            self.assertEqual(handle.read(), original)


class OpenArchiveLimitTests(CallSetup):
    blind = False

    def test_avaliacao_aberta_tambem_recusa(self):
        response = self.submit(file=SimpleUploadedFile('t.docx', _zip_bomb()))
        self.assertContains(response, 'grande demais depois de descompactado')
