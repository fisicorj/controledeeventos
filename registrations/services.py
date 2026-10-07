"""Regras de vagas e lista de espera."""

from django.utils import timezone

from activities.models import Activity

from . import notifications
from .models import Registration


def confirmed_count(activity):
    return activity.registrations.filter(status=Registration.Status.CONFIRMED).count()


def lock_activity(pk):
    """Trava a atividade até o fim da transação (SELECT ... FOR UPDATE).

    Todas as operações que mexem em vagas passam por aqui, então duas pessoas nunca ocupam
    a mesma vaga e a fila é sempre chamada na ordem certa.
    """
    return Activity.objects.select_for_update().get(pk=pk)


def promote_waitlist(activity):
    """Confirma os primeiros da fila enquanto houver vaga. Use dentro de transaction.atomic()
    com a atividade travada por lock_activity(). Retorna as inscrições promovidas."""
    if activity.status != Activity.Status.PUBLISHED or activity.end_date < timezone.now():
        return []
    free = activity.capacity - confirmed_count(activity)
    if free <= 0:
        return []
    queue = list(
        activity.registrations.filter(status=Registration.Status.WAITLISTED)
        .select_related('user', 'activity')
        .order_by('waitlisted_at', 'id')[:free]
    )
    for registration in queue:
        registration.status = Registration.Status.CONFIRMED
        registration.waitlisted_at = None
        registration.save(update_fields=['status', 'waitlisted_at'])
        notifications.notify_promoted(registration)
    return queue
