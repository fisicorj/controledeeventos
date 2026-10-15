"""Administração de contas pelo portal (Configurações > Usuários).

As ações passam por aqui, e não pelo /admin/ do Django, porque aqui valem as regras do sistema:
- ninguém altera a própria conta por este painel (evita se trancar fora ou se promover);
- o portal nunca fica sem administrador ativo;
- só superusuário mexe em conta de superusuário;
- a senha nunca é digitada pelo administrador: a pessoa recebe o link de nova senha;
- excluir é anonimizar (accounts/privacy.py), preservando inscrições, presenças e certificados;
- tudo fica registrado em UserChange (quem, quando, o quê).
"""

from django.contrib.auth.forms import PasswordResetForm
from django.db import transaction
from django.db.models import Q

from core.models import SiteSettings

from . import mfa
from .models import User, UserChange
from .sessions import end_sessions


class Refused(Exception):
    """A ação não é permitida para esta conta (mensagem pronta para a tela)."""


def _name(user):
    full = user.get_full_name()
    return f'{full} ({user.username})' if full else user.username


def _ip(request):
    from core.http import client_ip

    return (client_ip(request) or None) if request is not None else None


def record(actor, target, action, details=None, request=None):
    """O histórico guarda a conta pelo número, não pelo nome nem pelo e-mail: depois de uma exclusão, nada pessoal
    da pessoa fica aqui (a tela mostra o nome atual, que vira "Conta excluída"). O nome de quem fez continua:
    é ação de equipe, e a responsabilidade precisa ficar registrada (SEC-26 da 3ª auditoria)."""
    return UserChange.objects.create(
        actor=actor, actor_name=_name(actor), target=target, target_name=f'conta {target.pk}', action=action,
        details=details or {}, ip=_ip(request),
    )  # fmt: skip


def is_admin(user):
    return user.is_active and (user.is_superuser or user.role == User.Role.ADMIN)


def other_admins(target):
    return User.objects.filter(Q(is_superuser=True) | Q(role=User.Role.ADMIN), is_active=True).exclude(pk=target.pk)


def check_can_manage(actor, target):
    if actor.pk == target.pk:
        raise Refused('Você não pode alterar a própria conta por este painel. Peça a outro administrador.')
    if target.is_superuser and not actor.is_superuser:
        raise Refused('Só um superusuário pode alterar a conta de outro superusuário.')


def _keep_one_admin(target, still_admin):
    if is_admin(target) and not still_admin and not other_admins(target).exists():
        raise Refused('Esta é a última conta de administrador ativa. Promova outra pessoa antes.')


@transaction.atomic
def change_role(actor, target, role, request=None):
    check_can_manage(actor, target)
    if role not in User.Role.values:
        raise Refused('Papel inválido.')
    if role == target.role:
        return False
    _keep_one_admin(target, still_admin=target.is_superuser or role == User.Role.ADMIN)
    before = target.role
    target.role = role
    target.save(update_fields=['role'])
    labels = dict(User.Role.choices)
    record(actor, target, UserChange.Action.ROLE, {'antes': labels[before], 'depois': labels[role]}, request)
    return True


@transaction.atomic
def set_active(actor, target, active, request=None):
    check_can_manage(actor, target)
    if target.is_active == active:
        return False
    if not active:
        _keep_one_admin(target, still_admin=False)
    if active and target.is_deleted:
        raise Refused('Esta conta foi excluída (anonimizada) e não pode ser reativada.')
    target.is_active = active
    target.save(update_fields=['is_active'])
    ended = 0 if active else end_sessions(target)
    action = UserChange.Action.REACTIVATED if active else UserChange.Action.DEACTIVATED
    record(actor, target, action, {'sessoes_encerradas': ended} if not active else {}, request)
    return True


@transaction.atomic
def reset_mfa(actor, target, request=None):
    check_can_manage(actor, target)
    removed = mfa.reset(target, end_sessions=True)
    record(actor, target, UserChange.Action.MFA_RESET, {'cadastros_apagados': removed}, request)
    return removed


class _OneUserResetForm(PasswordResetForm):
    """O link vai para esta conta, mesmo que outra (não confirmada) use o mesmo e-mail."""

    def __init__(self, user, *args, **kwargs):
        self._user = user
        super().__init__({'email': user.email}, *args, **kwargs)

    def get_users(self, email):
        return iter([self._user])


def send_password_link(actor, target, request):
    check_can_manage(actor, target)
    if not target.is_active:
        raise Refused('A conta está desativada. Reative antes de mandar o link de nova senha.')
    if not target.email:
        raise Refused('A conta não tem e-mail.')
    form = _OneUserResetForm(target)
    form.is_valid()
    form.save(
        request=request,
        use_https=request.is_secure(),
        email_template_name='accounts/password_reset_email.txt',
        subject_template_name='accounts/password_reset_subject.txt',
        extra_email_context={'portal_name': SiteSettings.load().portal_name},
    )
    record(actor, target, UserChange.Action.PASSWORD_LINK, {}, request)


def resend_code(actor, target, request):
    from . import verification

    check_can_manage(actor, target)
    if target.email_verified:
        raise Refused('O e-mail desta conta já está confirmado.')
    sent, error = verification.send_code(request, target, target.email)
    if not sent:
        raise Refused(error)
    record(actor, target, UserChange.Action.CODE_RESENT, {}, request)


def can_receive(user):
    """Quem pode receber atividades e emissões: conta ativa de organizador, administrador ou superusuário."""
    return user.is_active and not user.is_deleted and (user.is_superuser or user.role != User.Role.PARTICIPANT)


@transaction.atomic
def transfer_ownership(actor, source, receiver, request=None):
    """Passa as atividades e as emissões avulsas de uma conta para outra (ex.: organizador que saiu). A assinatura
    dos certificados já emitidos não muda (privacy.keep_signature)."""
    from activities.models import Activity
    from certificates.models import CertificateBatch

    from .privacy import keep_signature

    check_can_manage(actor, source)
    if receiver.pk == source.pk:
        raise Refused('Escolha outra conta para receber.')
    if not can_receive(receiver):
        raise Refused('A conta que recebe precisa estar ativa e ser de organizador ou administrador.')
    if not source.is_deleted:
        keep_signature(source)
    activities = Activity.objects.filter(organizer=source).update(organizer=receiver)
    batches = CertificateBatch.objects.filter(created_by=source).update(created_by=receiver)
    if not activities and not batches:
        raise Refused('Esta conta não tem atividades nem emissões avulsas para transferir.')
    details = {'atividades': activities, 'emissoes_avulsas': batches, 'para': f'conta {receiver.pk}'}
    record(actor, source, UserChange.Action.TRANSFERRED, details, request)
    return activities, batches


@transaction.atomic
def delete_account(actor, target, request=None):
    from .privacy import anonymize

    check_can_manage(actor, target)
    _keep_one_admin(target, still_admin=False)
    previous_role = target.get_role_display()
    cancelled = anonymize(target)
    # Conta excluída não guarda papel de equipe: se um dia for reativada por engano, não volta com poderes.
    target.role, target.is_staff, target.is_superuser = User.Role.PARTICIPANT, False, False
    target.save(update_fields=['role', 'is_staff', 'is_superuser'])
    details = {'papel': previous_role, 'inscricoes_canceladas': cancelled}
    record(actor, target, UserChange.Action.ANONYMIZED, details, request)
    return cancelled
