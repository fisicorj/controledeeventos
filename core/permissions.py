"""Quem pode o quê: todas as regras de autorização do portal ficam aqui.

Papéis (accounts.models.User.Role):
- Administrador (ou superusuário): tudo, em todas as atividades e emissões avulsas, mais as configurações.
- Organizador: as próprias atividades e as próprias emissões avulsas.
- Participante: só os próprios dados. Pode fazer parte da equipe de check-in de uma atividade.

As views buscam o objeto, chamam a função daqui e respondem 403 quando ela devolve False.
"""

from accounts.models import User


def is_platform_admin(user):
    """Administradores da plataforma: superusuário ou papel "Administrador"."""
    return user.is_authenticated and (user.is_superuser or user.role == User.Role.ADMIN)


def is_organizer(user):
    """Pode usar o painel do organizador (administradores incluídos)."""
    return is_platform_admin(user) or (user.is_authenticated and user.role == User.Role.ORGANIZER)


# ---------- Atividades


def can_manage(user, activity=None):
    """Gerenciar a atividade (ou, sem atividade, criar atividades): administrador ou o organizador dela."""
    if is_platform_admin(user):
        return True
    return is_organizer(user) and (activity is None or activity.organizer_id == user.id)


def can_checkin(user, activity):
    """Registrar presença: quem gerencia a atividade ou faz parte da equipe de check-in dela."""
    if can_manage(user, activity):
        return True
    return user.is_authenticated and activity.checkin_staff.filter(pk=user.pk).exists()


def activities_for(user, queryset):
    """Atividades que aparecem no painel: todas para o administrador, as próprias para o organizador."""
    return queryset if is_platform_admin(user) else queryset.filter(organizer=user)


# ---------- Emissão avulsa de certificados


def can_manage_batch(user, batch=None):
    """Gerenciar uma emissão avulsa (ou, sem lote, criar): administrador ou quem emitiu."""
    if is_platform_admin(user):
        return True
    return is_organizer(user) and (batch is None or batch.created_by_id == user.id)


def batches_for(user, queryset):
    return queryset if is_platform_admin(user) else queryset.filter(created_by=user)
