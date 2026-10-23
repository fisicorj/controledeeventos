"""M-03 (prazo e lembrete dos avaliadores) e M-04 (distribuição automática dos trabalhos)."""

from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from activities.test_utils import make_user

from . import services
from .models import Review, ReviewerAreas, Submission
from .tests import CallSetup, pdf


class WorkMixin:
    def work(self, title, area='Redes', author=None, coauthors=''):
        return Submission.objects.create(
            call=self.call,
            author=author or make_user(f'autor-{title.lower().replace(" ", "-")}'),
            title=title,
            abstract='x',
            area=area,
            coauthors=coauthors,
            file=pdf(),
        )


class ReviewDeadlineTests(WorkMixin, CallSetup):
    def test_prazo_na_designacao_e_lembretes(self):
        work = self.work('Roteamento')
        mail.outbox.clear()
        self.assign(work, self.reviewer)
        review = Review.objects.get()
        self.assertAlmostEqual(review.due_at, timezone.now() + timedelta(days=14), delta=timedelta(minutes=1))
        self.assertIn('até', mail.outbox[0].body)

        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.remind_reviewers(), 0)  # longe do prazo
        later = review.due_at - timedelta(days=1)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.remind_reviewers(now=later), 1)
        self.assertIn('lembrete', mail.outbox[0].subject)
        self.assertIn('Roteamento', mail.outbox[0].body)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.remind_reviewers(now=later + timedelta(hours=5)), 0)  # já lembrado
        for days in (4, 7):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(services.remind_reviewers(now=later + timedelta(days=days)), 1)
        self.assertIn('atrasada', mail.outbox[-1].subject)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.remind_reviewers(now=later + timedelta(days=30)), 0)  # máximo de 3
        review.refresh_from_db()
        self.assertEqual(review.reminders_sent, 3)

    def test_entregue_ou_decidido_nao_e_lembrado_e_painel_mostra_atraso(self):
        done, waiting = self.work('Entregue'), self.work('Esperando')
        self.assign(done, self.reviewer)
        self.assign(waiting, self.reviewer)
        Review.objects.filter(submission=done).update(submitted_at=timezone.now())
        Review.objects.filter(submission=waiting).update(due_at=timezone.now() - timedelta(days=1))
        page = self.client.get(reverse('call_manage', args=[self.activity.pk]))
        self.assertContains(page, '1 atrasada')
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('reviewer_remind', args=[self.activity.pk, self.reviewer.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Esperando', mail.outbox[0].body)
        self.assertNotIn('Entregue', mail.outbox[0].body)
        self.client.force_login(self.reviewer)
        self.assertContains(self.client.get(reverse('my_reviews')), 'prazo terminou em')

    def test_nova_versao_renova_o_prazo(self):
        work = self.work('Versão')
        self.assign(work, self.reviewer)
        Review.objects.update(due_at=timezone.now() - timedelta(days=3), reminders_sent=2)
        services.resubmitted(work)
        review = Review.objects.get()
        self.assertGreater(review.due_at, timezone.now() + timedelta(days=13))
        self.assertEqual(review.reminders_sent, 0)


class AutoAssignTests(WorkMixin, CallSetup):
    def setUp(self):
        super().setUp()
        self.bia = make_user('bia', first_name='Bia', last_name='Redes')
        self.caio = make_user('caio', first_name='Caio', last_name='Dados')
        self.call.reviewers.add(self.bia, self.caio)
        ReviewerAreas.objects.create(call=self.call, reviewer=self.caio, areas='Inteligência artificial')

    def distribute(self, per_submission=2):
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse('call_auto_assign', args=[self.activity.pk]), {'per_submission': per_submission}, follow=True
            )

    def reviewers_of(self, work):
        return sorted(r.reviewer.username for r in work.reviews.all())

    def test_respeita_area_carga_e_conflito(self):
        net1, net2 = self.work('Redes 1'), self.work('Redes 2')
        ai = self.work('IA', area='Inteligência artificial', coauthors='Rui Revisor (Fatec)')
        response = self.distribute(per_submission=2)
        self.assertContains(response, 'avaliação(ões) designada(s)')
        self.assertEqual(self.reviewers_of(net1), ['bia', 'rev'])  # caio só avalia IA
        self.assertEqual(self.reviewers_of(net2), ['bia', 'rev'])
        self.assertEqual(self.reviewers_of(ai), ['bia', 'caio'])  # Rui é coautor: conflito
        self.assertTrue(all(r.due_at for r in Review.objects.all()))

    def test_avisa_quando_faltam_avaliadores_e_nao_repete(self):
        work = self.work('Redes 1')
        response = self.distribute(per_submission=3)
        self.assertContains(response, 'ficaram com menos avaliadores')
        self.assertEqual(self.reviewers_of(work), ['bia', 'rev'])
        response = self.distribute(per_submission=2)
        self.assertContains(response, 'Nada a distribuir')

    def test_carga_equilibrada(self):
        works = [self.work(f'Redes {i}') for i in range(4)]
        self.distribute(per_submission=1)
        counts = sorted(Review.objects.filter(submission__in=works).values_list('reviewer__username', flat=True))
        self.assertEqual(counts, ['bia', 'bia', 'rev', 'rev'])

    def test_areas_do_avaliador_pela_tela(self):
        self.work('Redes 1')
        self.client.force_login(self.org)
        self.client.post(
            reverse('reviewer_set_areas', args=[self.activity.pk, self.bia.pk]), {'areas': ['Redes', 'Inventada']}
        )
        self.assertEqual(ReviewerAreas.objects.get(reviewer=self.bia).area_list(), ['Redes'])
        self.assertContains(self.client.get(reverse('call_manage', args=[self.activity.pk])), 'Distribuir')
