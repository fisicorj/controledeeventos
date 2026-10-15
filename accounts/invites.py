"""Convite para a equipe (P3 da 3ª auditoria).

O administrador informa o e-mail e o papel (organizador ou administrador); a pessoa recebe um link, escolhe o
usuário e a senha e a conta já nasce com o papel e com o e-mail confirmado (o link chegou na caixa dela). No
primeiro acesso, o portal pede o cadastro da verificação em duas etapas, como para toda a equipe.

Regras: o link vale INVITE_DAYS dias e uma vez só; convite para e-mail que já tem conta confirmada é recusado
(nesse caso, basta mudar o papel da conta pelo painel); o administrador pode cancelar enquanto estiver aberto.
"""

import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import user_admin
from .models import StaffInvite, User, UserChange, email_taken

INVITE_DAYS = 7
STAFF_ROLES = (User.Role.ORGANIZER, User.Role.ADMIN)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create(actor, email, role, request=None):
    from registrations.notifications import absolute_url, queue

    email = email.strip().lower()
    if role not in STAFF_ROLES:
        raise user_admin.Refused('Convites são para organizador ou administrador.')
    if email_taken(email):
        raise user_admin.Refused(
            'Este e-mail já tem conta confirmada no portal. Abra a conta pelo painel e mude o papel dela.'
        )
    StaffInvite.objects.filter(email__iexact=email, accepted_at__isnull=True, cancelled_at__isnull=True).update(
        cancelled_at=timezone.now()
    )  # um convite aberto por e-mail: o novo substitui o anterior
    token = secrets.token_urlsafe(32)
    invite = StaffInvite.objects.create(
        email=email, role=role, token_hash=_hash(token), created_by=actor,
        expires_at=timezone.now() + timedelta(days=INVITE_DAYS),
    )  # fmt: skip
    context = {
        'role': invite.get_role_display().lower(),
        'inviter': actor.get_full_name() or actor.username,
        'url': absolute_url(reverse('invite_accept', args=[token])),
        'days': INVITE_DAYS,
    }
    queue(email, 'convite para a equipe do portal', 'staff_invite', context, group='invite')
    # O histórico (imutável) leva o número do convite, que liga ao e-mail sem guardá-lo (SEC-27 da 4ª auditoria).
    details = {'convite': invite.pk, 'papel': invite.get_role_display()}
    user_admin.record(actor, actor, UserChange.Action.INVITED, details, request)
    return invite


def find(token):
    invite = StaffInvite.objects.filter(token_hash=_hash(token or '')).first()
    return invite if invite and invite.is_open else None


def cancel(actor, invite):
    if invite.is_open:
        invite.cancelled_at = timezone.now()
        invite.save(update_fields=['cancelled_at'])
        return True
    return False


@transaction.atomic
def accept(invite, form):
    """Cria a conta com o papel do convite. form: InviteAcceptForm já validado."""
    from .models import release_email

    invite = StaffInvite.objects.select_for_update().get(pk=invite.pk)
    if not invite.is_open:
        raise user_admin.Refused('Este convite já foi usado, cancelado ou venceu. Peça um novo.')
    if email_taken(invite.email):
        raise user_admin.Refused('Já existe uma conta confirmada com este e-mail. Entre com ela.')
    release_email(invite.email)
    user = form.save(commit=False)
    user.email, user.role, user.email_verified = invite.email, invite.role, True
    user.privacy_accepted_at = timezone.now()
    user.save()
    invite.accepted_at, invite.accepted_user = timezone.now(), user
    invite.save(update_fields=['accepted_at', 'accepted_user'])
    details = {'convite': invite.pk, 'papel': invite.get_role_display()}
    user_admin.record(invite.created_by, user, UserChange.Action.INVITE_ACCEPTED, details)
    return user


KEEP_CLOSED_DAYS = 30


def purge_old():
    """Convites que não viraram conta (vencidos ou cancelados há mais de KEEP_CLOSED_DAYS dias) saem do banco,
    com o e-mail de quem nunca aceitou. Os aceitos ficam: ligam a conta a quem convidou. Roda na faxina diária."""
    from django.db.models import Q

    limit = timezone.now() - timedelta(days=KEEP_CLOSED_DAYS)
    old = StaffInvite.objects.filter(accepted_at__isnull=True).filter(
        Q(cancelled_at__lt=limit) | Q(cancelled_at__isnull=True, expires_at__lt=limit)
    )
    return old.delete()[0]
