"""Convite por e-mail para avaliar numa chamada (R4), para quem ainda não tem conta no portal.

A organização (ou a coordenação) informa o e-mail; a pessoa recebe um link que vale INVITE_DAYS dias e uma vez só.
Pelo link, ela cria a conta (já com o e-mail confirmado, porque o link chegou na caixa dela) ou entra com uma conta
que já tenha, e passa a fazer parte dos avaliadores da chamada. Convite aberto para o mesmo e-mail na mesma chamada
é substituído pelo novo. No banco fica só o hash do token.
"""

import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import ReviewerInvite

INVITE_DAYS = 14
KEEP_CLOSED_DAYS = 30
INVITES_PER_HOUR = 30  # por pessoa que convida: cada convite é um e-mail para um endereço qualquer (S-08)


class Refused(Exception):
    """O convite não pode ser usado; a mensagem explica por quê."""


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def within_limit(actor):
    """Conta um convite de quem convida. False: passou do limite desta hora."""
    from core import throttle

    return throttle.hit(INVITES_PER_HOUR, 3600, 'reviewer-invite-sent', actor.pk)


def create(call, email, actor):
    from registrations.notifications import absolute_url, queue

    email = email.strip().lower()
    ReviewerInvite.objects.filter(
        call=call, email__iexact=email, accepted_at__isnull=True, cancelled_at__isnull=True
    ).update(cancelled_at=timezone.now())
    token = secrets.token_urlsafe(32)
    invite = ReviewerInvite.objects.create(
        call=call,
        email=email,
        token_hash=_hash(token),
        created_by=actor,
        expires_at=timezone.now() + timedelta(days=INVITE_DAYS),
    )
    queue(
        email,
        f'convite para avaliar trabalhos em {call.activity.title}',
        'reviewer_invite',
        {
            'activity': call.activity,
            'call': call,
            'inviter': actor.get_full_name() or actor.username,
            'url': absolute_url(reverse('reviewer_invite_accept', args=[token])),
            'days': INVITE_DAYS,
        },
        group=f'call:{call.pk}:team',
    )
    return invite


def find(token):
    invite = ReviewerInvite.objects.select_related('call__activity').filter(token_hash=_hash(token or '')).first()
    return invite if invite and invite.is_open else None


def cancel(invite):
    if invite.is_open:
        invite.cancelled_at = timezone.now()
        invite.save(update_fields=['cancelled_at'])
        return True
    return False


@transaction.atomic
def accept(invite, user):
    """A pessoa (conta nova ou existente) entra para os avaliadores da chamada."""
    invite = ReviewerInvite.objects.select_for_update().select_related('call').get(pk=invite.pk)
    if not invite.is_open:
        raise Refused('Este convite já foi usado, cancelado ou venceu. Peça um novo à organização do evento.')
    invite.call.reviewers.add(user)
    invite.accepted_at, invite.accepted_user = timezone.now(), user
    invite.save(update_fields=['accepted_at', 'accepted_user'])
    from .notifications import notify_invite_accepted

    transaction.on_commit(lambda: notify_invite_accepted(invite))
    return invite.call


@transaction.atomic
def create_account(invite, form):
    """Conta nova pelo convite: o e-mail vem do convite e já nasce confirmado. form: InviteAcceptForm validado."""
    from accounts.models import User, email_taken, release_email

    if email_taken(invite.email):
        raise Refused('Já existe uma conta confirmada com este e-mail. Entre com ela para aceitar o convite.')
    release_email(invite.email)
    user = form.save(commit=False)
    user.email, user.role, user.email_verified = invite.email, User.Role.PARTICIPANT, True
    user.privacy_accepted_at = timezone.now()
    user.save()
    accept(invite, user)
    return user


def purge_old():
    """Convites não aceitos, vencidos ou cancelados há mais de KEEP_CLOSED_DAYS dias, saem com o e-mail."""
    from django.db.models import Q

    limit = timezone.now() - timedelta(days=KEEP_CLOSED_DAYS)
    old = ReviewerInvite.objects.filter(accepted_at__isnull=True).filter(
        Q(cancelled_at__lt=limit) | Q(cancelled_at__isnull=True, expires_at__lt=limit)
    )
    return old.delete()[0]
