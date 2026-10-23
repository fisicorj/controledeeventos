"""P3 da revisão v8: duas etapas para a coordenação (S-05), convite aceito com outra conta (S-06) e limite de
convites de avaliador (S-08)."""

import re

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from accounts import mfa
from activities.test_utils import make_user

from . import invites
from .models import ReviewerInvite
from .tests import CallSetup


class CoordinationMfaTests(CallSetup):
    def test_coordenacao_precisa_das_duas_etapas(self):
        person = make_user('coord')
        self.assertFalse(mfa.applies_to(make_user('coord_ref')))
        self.call.coordinators.add(person)
        person = type(person).objects.get(pk=person.pk)  # sem o valor guardado no objeto
        self.assertTrue(mfa.applies_to(person))
        self.assertFalse(mfa.applies_to(self.reviewer))  # avaliador sozinho, não

    @override_settings(MFA_REQUIRED=True)
    def test_coordenacao_e_levada_ao_cadastro_do_autenticador(self):
        person = make_user('coord')
        self.call.coordinators.add(person)
        self.client.force_login(person)
        response = self.client.get(reverse('call_manage', args=[self.activity.pk]))
        self.assertRedirects(response, reverse('mfa_setup'), fetch_redirect_response=False)

    @override_settings(MFA_REQUIRED=True)
    def test_participante_comum_nao_e_levado(self):
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(reverse('my_reviews')).status_code, 200)

    def test_email_da_coordenacao_avisa_das_duas_etapas(self):
        person = make_user('coord', email_verified=True)
        self.client.force_login(self.org)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('coordinator_add', args=[self.activity.pk]), {'person': 'coord'})
        self.assertTrue(self.call.coordinators.filter(pk=person.pk).exists())
        self.assertIn('verificação em duas etapas', mail.outbox[-1].body)


class InviteOtherAccountTests(CallSetup):
    def invite(self, email='externa@univ.br'):
        self.client.force_login(self.org)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('reviewer_add', args=[self.activity.pk]), {'person': email})
        return re.search(r'/convite-avaliador/([^/]+)/', mail.outbox[-1].body).group(1)

    def test_outra_conta_ve_o_email_do_convite_e_precisa_confirmar(self):
        url = reverse('reviewer_invite_accept', args=[self.invite()])
        other = make_user('outra')  # outra@exemplo.com
        self.client.force_login(other)
        page = self.client.get(url)
        self.assertContains(page, 'externa@univ.br')
        self.assertContains(page, 'name="confirm_other"')
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Marque a confirmação')
        self.assertFalse(self.call.reviewers.filter(pk=other.pk).exists())
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {'confirm_other': '1'})
        self.assertTrue(self.call.reviewers.filter(pk=other.pk).exists())
        notice = [m for m in mail.outbox if 'convite de avaliador aceito' in m.subject]
        self.assertEqual([m.to for m in notice], [[self.org.email]])
        self.assertIn('externa@univ.br', notice[0].body)
        self.assertIn('outra@exemplo.com', notice[0].body)

    def test_mesma_conta_aceita_sem_confirmacao_extra(self):
        url = reverse('reviewer_invite_accept', args=[self.invite('mesma@univ.br')])
        same = make_user('mesma', email_verified=False)
        same.email = 'Mesma@univ.br'
        same.save(update_fields=['email'])
        self.client.force_login(same)
        self.assertNotContains(self.client.get(url), 'name="confirm_other"')
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url)
        self.assertTrue(self.call.reviewers.filter(pk=same.pk).exists())
        notice = [m for m in mail.outbox if 'convite de avaliador aceito' in m.subject]
        self.assertEqual(len(notice), 1)
        self.assertNotIn('Atenção', notice[0].body)


class InviteLimitTests(CallSetup):
    def test_limite_de_convites_por_hora(self):
        self.client.force_login(self.org)
        url = reverse('reviewer_add', args=[self.activity.pk])
        for i in range(invites.INVITES_PER_HOUR):
            self.client.post(url, {'person': f'pessoa{i}@univ.br'})
        self.assertEqual(ReviewerInvite.objects.count(), invites.INVITES_PER_HOUR)
        response = self.client.post(url, {'person': 'mais@univ.br'}, follow=True)
        self.assertContains(response, 'Limite de 30 convites por hora')
        self.assertEqual(ReviewerInvite.objects.count(), invites.INVITES_PER_HOUR)
        invite = ReviewerInvite.objects.first()
        response = self.client.post(reverse('reviewer_invite_resend', args=[self.activity.pk, invite.pk]), follow=True)
        self.assertContains(response, 'Limite de 30 convites por hora')
        self.assertFalse(ReviewerInvite.objects.filter(email=invite.email, cancelled_at__isnull=False).exists())
