"""Regras de vagas e lista de espera."""

from datetime import timedelta

from django.db.models import Count
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


def category_counts(activity):
    """Confirmados por tipo de vaga: {id_da_categoria (ou None): quantidade}."""
    rows = (
        activity.registrations.filter(status=Registration.Status.CONFIRMED)
        .values('category_id')
        .annotate(n=Count('id'))
    )
    return {row['category_id']: row['n'] for row in rows}


def has_room(activity, category=None, counts=None):
    """Há vaga na atividade e, quando informado, no tipo de vaga?"""
    counts = category_counts(activity) if counts is None else counts
    if sum(counts.values()) >= activity.capacity:
        return False
    return category is None or counts.get(category.id, 0) < category.capacity


OFFER_MARGIN = timedelta(hours=1)  # o prazo de confirmação termina pelo menos 1 hora antes do início


def offer_deadline(activity, now=None):
    """Até quando quem sai da fila tem para confirmar; None quando a confirmação é na hora (prazo 0, ou a
    atividade começa antes de o prazo acabar)."""
    now = now or timezone.now()
    if not activity.waitlist_offer_hours:
        return None
    deadline = now + timedelta(hours=activity.waitlist_offer_hours)
    return deadline if deadline <= activity.start_date - OFFER_MARGIN else None


def expire_offers(activity):
    """Vagas oferecidas e não confirmadas no prazo: a inscrição é cancelada e a vaga volta para a fila.
    Use com a atividade travada (lock_activity). Devolve as inscrições canceladas."""
    now = timezone.now()
    expired = list(
        activity.registrations.filter(status=Registration.Status.CONFIRMED, offer_expires_at__lte=now).select_related(
            'user', 'activity'
        )
    )
    for registration in expired:
        registration.status, registration.cancelled_at, registration.offer_expires_at = (
            Registration.Status.CANCELLED, now, None
        )  # fmt: skip
        registration.save(update_fields=['status', 'cancelled_at', 'offer_expires_at'])
        notifications.notify_offer_expired(registration)
    return expired


def expire_all_offers():
    """Varre todas as atividades com prazo vencido (comando agendado e serviço da fila). Devolve quantas."""
    from django.db import transaction

    # Só atividades publicadas: cancelada ou finalizada não tem mais vaga a oferecer (e a pessoa já foi avisada).
    activity_ids = set(
        Registration.objects.filter(
            status=Registration.Status.CONFIRMED,
            offer_expires_at__lte=timezone.now(),
            activity__status=Activity.Status.PUBLISHED,
        ).values_list('activity_id', flat=True)
    )
    total = 0
    for activity_id in activity_ids:
        with transaction.atomic():
            activity = lock_activity(activity_id)
            total += len(expire_offers(activity))
            promote_waitlist(activity, expire=False)
    return total


def clear_offers(activity):
    """Atividade cancelada ou finalizada: as vagas oferecidas deixam de ter prazo (nada mais vai vencer)."""
    pending = activity.registrations.filter(status=Registration.Status.CONFIRMED, offer_expires_at__isnull=False)
    return pending.update(offer_expires_at=None)


def confirm_offer(registration):
    """A pessoa confirmou a vaga oferecida. Devolve False se o prazo já tinha acabado."""
    from django.db import transaction

    with transaction.atomic():
        activity = lock_activity(registration.activity_id)
        registration.refresh_from_db()
        if not registration.offer_pending:
            return registration.status == Registration.Status.CONFIRMED
        if registration.offer_expires_at <= timezone.now():
            expire_offers(activity)
            promote_waitlist(activity, expire=False)
            return False
        registration.offer_expires_at = None
        registration.save(update_fields=['offer_expires_at'])
        notifications.notify_registration_confirmed(registration)
        return True


def promote_waitlist(activity, expire=True):
    """Confirma os primeiros da fila enquanto houver vaga. Use dentro de transaction.atomic()
    com a atividade travada por lock_activity(). Retorna as inscrições promovidas.

    Com tipos de vaga, cada pessoa da fila só sobe se houver vaga no tipo dela; quem está atrás,
    num tipo com vaga, pode passar na frente.

    Com prazo de confirmação (waitlist_offer_hours), quem sobe recebe a vaga reservada e precisa confirmar até o
    prazo; antes de chamar a fila, as ofertas vencidas são canceladas."""
    if activity.status != Activity.Status.PUBLISHED or activity.end_date < timezone.now():
        return []
    if expire:
        expire_offers(activity)
    counts = category_counts(activity)
    if sum(counts.values()) >= activity.capacity:
        return []
    queue = (
        activity.registrations.filter(status=Registration.Status.WAITLISTED)
        .select_related('user', 'activity', 'category')
        .order_by('waitlisted_at', 'id')
    )
    promoted = []
    for registration in queue:
        if not has_room(activity, registration.category, counts):
            if sum(counts.values()) >= activity.capacity:
                break
            continue
        deadline = offer_deadline(activity)
        registration.status = Registration.Status.CONFIRMED
        registration.waitlisted_at = None
        registration.offer_expires_at = deadline
        registration.save(update_fields=['status', 'waitlisted_at', 'offer_expires_at'])
        counts[registration.category_id] = counts.get(registration.category_id, 0) + 1
        if deadline:
            notifications.notify_offer(registration)
        else:
            notifications.notify_promoted(registration)
        promoted.append(registration)
    return promoted
