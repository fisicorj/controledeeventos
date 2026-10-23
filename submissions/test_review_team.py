"""R1 a R6: menu de avaliações, coordenação da chamada, reaproveitar avaliadores, convite por e-mail, recusa da
designação e critérios de avaliação."""

import re
from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.test_utils import PASSWORD, make_activity, make_user

from . import services
from .models import CallForPapers, Review, ReviewDecline, ReviewerAreas, ReviewerInvite
from .test_reviewers import WorkMixin
from .tests import CallSetup


class ReviewMenuTests(WorkMixin, CallSetup):
    def test_menu_aparece_para_avaliador_com_pendentes(self):
        self.client.force_login(self.reviewer)
        page = self.client.get(reverse('activity_list'))
        self.assertContains(page, '>Avaliações')  # já está na lista de avaliadores da chamada
        work = self.work('Redes 1')
        self.assign(work, self.reviewer)
        self.client.force_login(self.reviewer)
        page = self.client.get(reverse('activity_list'))
        self.assertContains(page, '1 pendente')

    def test_menu_nao_aparece_para_quem_nao_avalia(self):
        self.client.force_login(make_user('qualquer'))
        self.assertNotContains(self.client.get(reverse('activity_list')), '>Avaliações')


class CoordinationTests(WorkMixin, CallSetup):
    def setUp(self):
        super().setUp()
        self.coordinator = make_user('coord', first_name='Cora', last_name='Coordena')

    def add_coordinator(self):
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse('coordinator_add', args=[self.activity.pk]), {'person': 'coord'}, follow=True
            )

    def test_organizacao_inclui_e_coordenacao_cuida_da_chamada(self):
        mail.outbox.clear()
        self.assertContains(self.add_coordinator(), 'entrou na coordenação')
        self.assertTrue(any(m.to == ['coord@exemplo.com'] for m in mail.outbox))
        work = self.work('Redes 1')
        self.client.force_login(self.coordinator)
        self.assertEqual(self.client.get(reverse('call_manage', args=[self.activity.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('submission_manage', args=[work.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('submission_file', args=[work.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('call_edit', args=[self.activity.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse('my_reviews')), 'Chamadas que você coordena')
        # Decide como a organização.
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse('submission_manage', args=[work.pk]), {'action': 'decide', 'decision': 'REJECTED', 'note': ''}
            )
        work.refresh_from_db()
        self.assertEqual(work.status, 'REJECTED')

    def test_coordenacao_nao_ve_a_gestao_do_evento_nem_muda_a_coordenacao(self):
        self.add_coordinator()
        self.client.force_login(self.coordinator)
        self.assertEqual(self.client.get(reverse('activity_manage', args=[self.activity.pk])).status_code, 403)
        response = self.client.post(reverse('coordinator_add', args=[self.activity.pk]), {'person': 'rev'})
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(self.client.get(reverse('call_manage', args=[self.activity.pk])), 'activity_manage')

    def test_quem_nao_coordena_nao_entra(self):
        self.client.force_login(self.coordinator)
        self.assertEqual(self.client.get(reverse('call_manage', args=[self.activity.pk])).status_code, 403)


class CopyReviewersTests(CallSetup):
    def test_trazer_avaliadores_com_as_areas_em_comum_e_duplicar_leva_a_chamada(self):
        from activities.operations import duplicate_activity

        other = make_activity(self.org, title='Congresso 2025')
        old = CallForPapers.objects.create(
            activity=other, opens_at=timezone.now() - timedelta(days=60), closes_at=other.end_date,
            instructions='x', areas='Redes\nBanco de dados',
        )  # fmt: skip
        bia, caio = make_user('bia'), make_user('caio')
        old.reviewers.add(bia, caio)
        ReviewerAreas.objects.create(call=old, reviewer=bia, areas='Redes\nBanco de dados')
        ReviewerAreas.objects.create(call=old, reviewer=caio, areas='Banco de dados')
        self.client.force_login(self.org)
        response = self.client.post(reverse('reviewers_copy', args=[self.activity.pk]), {'source': old.pk}, follow=True)
        self.assertContains(response, '2 avaliador(es) trazido(s)')
        self.assertContains(response, 'ficaram com todas')  # caio: "Banco de dados" não existe aqui
        self.assertEqual(ReviewerAreas.objects.get(call=self.call, reviewer=bia).area_list(), ['Redes'])
        self.assertFalse(ReviewerAreas.objects.filter(call=self.call, reviewer=caio).exists())

        self.call.coordinators.add(caio)
        self.call.criteria = 'Originalidade\nClareza'
        self.call.save()
        copy = duplicate_activity(self.activity, 'Congresso de TI 2027', self.activity.start_date + timedelta(days=364))
        new_call = CallForPapers.objects.get(activity=copy)
        self.assertEqual(new_call.closes_at - self.call.closes_at, timedelta(days=364))
        self.assertEqual(set(new_call.reviewers.values_list('username', flat=True)), {'rev', 'bia', 'caio'})
        self.assertEqual(list(new_call.coordinators.values_list('username', flat=True)), ['caio'])
        self.assertEqual(new_call.criteria_list(), ['Originalidade', 'Clareza'])
        self.assertFalse(new_call.submissions.exists())

    def test_nao_traz_de_chamada_que_nao_cuida(self):
        stranger = make_user('outra', User.Role.ORGANIZER)
        other = make_activity(stranger, title='Outro')
        foreign = CallForPapers.objects.create(
            activity=other, opens_at=timezone.now(), closes_at=other.end_date, instructions='x'
        )
        self.client.force_login(self.org)
        response = self.client.post(reverse('reviewers_copy', args=[self.activity.pk]), {'source': foreign.pk})
        self.assertEqual(response.status_code, 403)


class ReviewerInviteTests(CallSetup):
    def invite(self, email='externa@univ.br'):
        self.client.force_login(self.org)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('reviewer_add', args=[self.activity.pk]), {'person': email}, follow=True
            )
        token = re.search(r'/convite-avaliador/([^/]+)/', mail.outbox[-1].body).group(1)
        return response, token

    def test_convite_cria_conta_confirmada_e_vira_avaliadora(self):
        response, token = self.invite()
        self.assertContains(response, 'enviamos um convite')
        self.assertContains(response, 'externa@univ.br')  # pendente na lista
        self.client.logout()
        url = reverse('reviewer_invite_accept', args=[token])
        self.assertContains(self.client.get(url), 'externa@univ.br')
        response = self.client.post(url, {
            'username': 'externa', 'first_name': 'Eva', 'last_name': 'Externa',
            'password1': PASSWORD, 'password2': PASSWORD, 'accept_privacy': 'on',
        })  # fmt: skip
        self.assertRedirects(response, reverse('my_reviews'))
        user = User.objects.get(username='externa')
        self.assertTrue(user.email_verified)
        self.assertEqual(user.role, User.Role.PARTICIPANT)
        self.assertTrue(self.call.reviewers.filter(pk=user.pk).exists())
        self.assertEqual(self.client.get(url).status_code, 404)  # uma vez só

    def test_conta_existente_aceita_logada(self):
        _, token = self.invite()
        someone = make_user('alguem')
        self.client.force_login(someone)
        self.client.post(reverse('reviewer_invite_accept', args=[token]), {'confirm_other': '1'})
        self.assertTrue(self.call.reviewers.filter(pk=someone.pk).exists())

    def test_reenviar_invalida_o_link_anterior_e_cancelar(self):
        _, first = self.invite()
        invite = ReviewerInvite.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('reviewer_invite_resend', args=[self.activity.pk, invite.pk]))
        self.client.logout()
        self.assertEqual(self.client.get(reverse('reviewer_invite_accept', args=[first])).status_code, 404)
        self.client.force_login(self.org)
        latest = ReviewerInvite.objects.filter(cancelled_at__isnull=True).get()
        self.client.post(reverse('reviewer_invite_cancel', args=[self.activity.pk, latest.pk]))
        latest.refresh_from_db()
        self.assertIsNotNone(latest.cancelled_at)

    def test_conta_confirmada_entra_direto_sem_convite(self):
        mail.outbox.clear()
        make_user('ja', email_verified=True)
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('reviewer_add', args=[self.activity.pk]), {'person': 'ja@exemplo.com'}, follow=True
            )
        self.assertContains(response, 'entrou como avaliador')
        self.assertFalse(ReviewerInvite.objects.exists())
        self.assertIn('incluído como avaliador', mail.outbox[0].subject)


class DeclineTests(WorkMixin, CallSetup):
    def test_recusar_tira_a_designacao_avisa_e_nao_volta_na_distribuicao(self):
        work = self.work('Redes 1')
        self.assign(work, self.reviewer)
        review = Review.objects.get()
        self.client.force_login(self.reviewer)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('review_form', args=[review.pk]),
                {'decline': '1', 'reason': 'CONFLICT', 'note': 'Orientei o autor.'},
                follow=True,
            )
        self.assertContains(response, 'Recusa registrada')
        self.assertFalse(Review.objects.exists())
        decline = ReviewDecline.objects.get()
        self.assertEqual((decline.reviewer, decline.reason), (self.reviewer, 'CONFLICT'))
        self.assertTrue(any('avaliador recusou' in m.subject for m in mail.outbox))
        created, short = services.auto_assign(self.call, 1)
        self.assertEqual((created, short), (0, [work]))  # o único avaliador recusou
        self.client.force_login(self.org)
        self.assertContains(self.client.get(reverse('submission_manage', args=[work.pk])), 'conflito de interesse')

    def test_avaliacao_entregue_nao_pode_ser_recusada(self):
        work = self.work('Redes 1')
        self.assign(work, self.reviewer)
        review = Review.objects.get()
        Review.objects.update(submitted_at=timezone.now(), recommendation='ACCEPT', score=4)
        self.client.force_login(self.reviewer)
        self.client.post(reverse('review_form', args=[review.pk]), {'decline': '1', 'reason': 'UNAVAILABLE'})
        self.assertTrue(Review.objects.exists())
        self.assertFalse(ReviewDecline.objects.exists())


class CriteriaTests(WorkMixin, CallSetup):
    def setUp(self):
        super().setUp()
        self.call.criteria = 'Originalidade\nMetodologia\nClareza'
        self.call.save()
        self.work_item = self.work('Redes 1')
        self.assign(self.work_item, self.reviewer)
        self.review = Review.objects.get()

    def test_nota_por_criterio_e_media(self):
        self.client.force_login(self.reviewer)
        page = self.client.get(reverse('review_form', args=[self.review.pk]))
        self.assertContains(page, 'Metodologia')
        self.assertNotContains(page, 'Nota (1 a 5)')
        data = {
            'recommendation': 'ACCEPT', 'comments_to_author': 'Bom.',
            'criterion_0': 5, 'criterion_1': 3, 'criterion_2': 4,
        }  # fmt: skip
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('review_form', args=[self.review.pk]), data)
        self.review.refresh_from_db()
        self.assertEqual(self.review.criteria_scores, {'Originalidade': 5, 'Metodologia': 3, 'Clareza': 4})
        self.assertEqual(self.review.score, 4)
        self.client.force_login(self.org)
        self.assertContains(self.client.get(reverse('submission_manage', args=[self.work_item.pk])), 'Metodologia: 3')
        csv = self.client.get(reverse('submissions_csv', args=[self.activity.pk])).content.decode()
        self.assertIn('Média: Originalidade', csv)
        self.assertIn('5,0', csv)

    def test_criterio_obrigatorio_e_trava_depois_da_primeira_avaliacao(self):
        from .forms import CallForm

        self.client.force_login(self.reviewer)
        response = self.client.post(
            reverse('review_form', args=[self.review.pk]),
            {'recommendation': 'ACCEPT', 'comments_to_author': 'x', 'criterion_0': 5},
        )
        self.assertContains(response, 'Dê uma nota de 1 a 5 para')
        self.assertFalse(CallForm(instance=self.call).fields['criteria'].disabled)
        Review.objects.update(submitted_at=timezone.now())
        self.assertTrue(CallForm(instance=self.call).fields['criteria'].disabled)
