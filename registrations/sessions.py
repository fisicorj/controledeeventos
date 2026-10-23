"""Inscrição por sessão (Activity.per_session).

A pessoa se inscreve no evento (com as vagas do evento, como sempre) e escolhe as sessões que vai assistir.
Cada sessão tem as suas vagas e a sua lista de espera, chamada por ordem de chegada assim que uma vaga abre.
Dá para entrar e sair de uma sessão até ela começar. O certificado sai por sessão (certificates/services.py).

Todas as operações travam a atividade (services.lock_activity), como as inscrições no evento: duas pessoas
nunca ocupam a mesma vaga da sessão e a fila é sempre chamada na ordem certa.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from activities.models import Activity

from . import notifications
from .models import Registration, SessionEnrollment

Status = SessionEnrollment.Status
ACTIVE = (Status.CONFIRMED, Status.WAITLISTED)


class Refused(Exception):
    """A escolha não pode ser feita; a mensagem explica por quê."""


def counts(activity):
    """Confirmados e fila de cada sessão, numa consulta: {id_da_sessão: (confirmados, na_fila)}."""
    rows = (
        SessionEnrollment.objects.filter(session__activity=activity, status__in=ACTIVE)
        .values('session_id')
        .annotate(
            confirmed=Count('id', filter=Q(status=Status.CONFIRMED)),
            waiting=Count('id', filter=Q(status=Status.WAITLISTED)),
        )
    )
    return {row['session_id']: (row['confirmed'], row['waiting']) for row in rows}


def with_room(activity, sessions=None):
    """Sessões com taken, remaining e is_open (ainda não começou) preenchidos, para as telas."""
    sessions = list(activity.sessions.all() if sessions is None else sessions)
    numbers = counts(activity)
    now = timezone.now()
    for session in sessions:
        session.taken, session.waiting = numbers.get(session.pk, (0, 0))
        session.remaining = None if session.capacity is None else max(session.capacity - session.taken, 0)
        session.is_open = session.start_at > now
        session.waitlist_enabled = activity.waitlist_enabled
    return sessions


def has_seat(session, taken):
    return session.capacity is None or taken < session.capacity


def can_choose(registration):
    """Pode escolher sessões: inscrição confirmada (não só oferecida) numa atividade publicada por sessão."""
    activity = registration.activity
    return (
        activity.per_session
        and activity.status == Activity.Status.PUBLISHED
        and registration.status == Registration.Status.CONFIRMED
        and registration.offer_expires_at is None
    )


def active_enrollments(registration):
    return registration.session_enrollments.filter(status__in=ACTIVE).select_related('session')


def _check_conflict(registration, session, chosen=()):
    """Recusa a sessão que acontece ao mesmo tempo que outra já escolhida."""
    others = [e.session for e in active_enrollments(registration) if e.session_id != session.pk] + list(chosen)
    for other in others:
        if other.pk != session.pk and session.overlaps(other):
            raise Refused(
                f'"{session.title}" acontece no mesmo horário de "{other.title}", que você já escolheu. '
                'Saia de uma delas antes.'
            )


def _enroll_locked(registration, session, activity, chosen=()):
    """Entra na sessão (confirmada ou na fila). A atividade já está travada pelo chamador."""
    if session.activity_id != registration.activity_id:
        raise Refused('Esta sessão é de outra atividade.')
    if not can_choose(registration):
        raise Refused('A sua inscrição no evento precisa estar confirmada para escolher as sessões.')
    if session.start_at <= timezone.now():
        raise Refused(f'"{session.title}" já começou: não dá mais para entrar.')
    enrollment = SessionEnrollment.objects.filter(registration=registration, session=session).first()
    if enrollment and enrollment.is_active:
        return enrollment
    _check_conflict(registration, session, chosen)
    taken = session.enrollments.filter(status=Status.CONFIRMED).count()
    full = not has_seat(session, taken)
    if full and not activity.waitlist_enabled:
        raise Refused(f'"{session.title}" está lotada.')
    if enrollment is None:
        enrollment = SessionEnrollment(registration=registration, session=session)
    enrollment.status = Status.WAITLISTED if full else Status.CONFIRMED
    enrollment.waitlisted_at = timezone.now() if full else None
    enrollment.cancelled_at = None
    enrollment.save()
    return enrollment


def choose(registration, sessions):
    """Entra em várias sessões de uma vez (na inscrição). A atividade já está travada pelo chamador.

    Devolve (inscrições nas sessões, recusas): uma sessão recusada (lotada, no mesmo horário de outra) não
    impede as outras."""
    activity = registration.activity
    done, refused = [], []
    for session in sorted(sessions, key=lambda s: s.start_at):
        try:
            done.append(_enroll_locked(registration, session, activity, chosen=[e.session for e in done]))
        except Refused as exc:
            refused.append(str(exc))
    return done, refused


@transaction.atomic
def enroll(registration, session):
    """Entra numa sessão depois da inscrição (Minhas inscrições). Levanta Refused com o motivo."""
    from .services import lock_activity

    activity = lock_activity(registration.activity_id)
    registration.refresh_from_db()
    registration.activity = activity
    return _enroll_locked(registration, session, activity)


@transaction.atomic
def leave(enrollment):
    """Sai da sessão (ou da fila dela) antes de começar; a vaga vai para o primeiro da fila."""
    from .services import lock_activity

    lock_activity(enrollment.session.activity_id)
    enrollment.refresh_from_db()
    if not enrollment.is_active:
        return False
    if enrollment.session.start_at <= timezone.now():
        raise Refused(f'"{enrollment.session.title}" já começou: a escolha não pode mais ser desfeita.')
    _cancel(enrollment)
    return True


def _cancel(enrollment):
    was_confirmed = enrollment.status == Status.CONFIRMED
    enrollment.status = Status.CANCELLED
    enrollment.waitlisted_at = None
    enrollment.cancelled_at = timezone.now()
    enrollment.save(update_fields=['status', 'waitlisted_at', 'cancelled_at'])
    if was_confirmed:
        promote(enrollment.session)


def cancel_all(registration):
    """A inscrição no evento foi cancelada: sai de todas as sessões que ainda não começaram e libera as vagas.
    As sessões que já aconteceram ficam como estão (presença e certificado dependem delas)."""
    now = timezone.now()
    for enrollment in active_enrollments(registration):
        if enrollment.session.start_at > now:
            _cancel(enrollment)


def offer_deadline(session, now=None):
    """Até quando quem sai da fila da sessão tem para confirmar; None quando a confirmação é na hora (prazo 0 na
    atividade, ou a sessão começa antes de o prazo acabar). A mesma regra da fila do evento."""
    from .services import OFFER_MARGIN

    now = now or timezone.now()
    hours = session.activity.waitlist_offer_hours
    if not hours:
        return None
    deadline = now + timedelta(hours=hours)
    return deadline if deadline <= session.start_at - OFFER_MARGIN else None


def expire_offers(session, now=None):
    """Vagas da sessão oferecidas e não confirmadas no prazo: saem e a vaga volta para a fila. Devolve quantas."""
    now = now or timezone.now()
    expired = list(
        session.enrollments.filter(status=Status.CONFIRMED, offer_expires_at__lte=now).select_related(
            'registration__user', 'registration__activity', 'session'
        )
    )
    for enrollment in expired:
        enrollment.status = Status.CANCELLED
        enrollment.cancelled_at = now
        enrollment.offer_expires_at = None
        enrollment.save(update_fields=['status', 'cancelled_at', 'offer_expires_at'])
        notifications.notify_session_offer_expired(enrollment)
    return len(expired)


def promote(session):
    """Chama a fila da sessão enquanto houver vaga (só antes de a sessão começar). Devolve quem entrou.

    Com prazo de confirmação na atividade, quem sobe recebe a vaga reservada e confirma pelo link do e-mail; senão,
    ela passa para o próximo (expire_offers). Antes de chamar a fila, as ofertas vencidas saem."""
    if session.start_at <= timezone.now():
        return []
    expire_offers(session)
    taken = session.enrollments.filter(status=Status.CONFIRMED).count()
    promoted = []
    queue = (
        session.enrollments.filter(status=Status.WAITLISTED, registration__status=Registration.Status.CONFIRMED)
        .select_related('registration__user', 'registration__activity', 'session')
        .order_by('waitlisted_at', 'id')
    )
    for enrollment in queue:
        if not has_seat(session, taken):
            break
        deadline = offer_deadline(session)
        enrollment.status = Status.CONFIRMED
        enrollment.waitlisted_at = None
        enrollment.offer_expires_at = deadline
        enrollment.save(update_fields=['status', 'waitlisted_at', 'offer_expires_at'])
        taken += 1
        promoted.append(enrollment)
        if deadline:
            notifications.notify_session_offer(enrollment)
        else:
            notifications.notify_session_promoted(enrollment)
    return promoted


@transaction.atomic
def confirm_offer(enrollment):
    """A pessoa confirmou a vaga oferecida na sessão. Devolve False se o prazo já tinha acabado."""
    from .services import lock_activity

    lock_activity(enrollment.session.activity_id)
    enrollment.refresh_from_db()
    if not enrollment.offer_pending:
        return enrollment.status == Status.CONFIRMED
    if enrollment.offer_expires_at <= timezone.now():
        promote(enrollment.session)  # tira a oferta vencida e chama o próximo
        return False
    enrollment.offer_expires_at = None
    enrollment.save(update_fields=['offer_expires_at'])
    return True


def expire_all_offers():
    """Varre as sessões com oferta vencida (worker, a cada 5 minutos). Devolve quantas ofertas saíram."""
    from activities.models import Session

    from .services import lock_activity

    session_ids = set(
        SessionEnrollment.objects.filter(
            status=Status.CONFIRMED,
            offer_expires_at__lte=timezone.now(),
            session__activity__status=Activity.Status.PUBLISHED,
        ).values_list('session_id', flat=True)
    )
    total = 0
    for session in Session.objects.filter(pk__in=session_ids).select_related('activity'):
        with transaction.atomic():
            lock_activity(session.activity_id)
            total += expire_offers(session)
            promote(session)
    return total


def describe(enrollments, refused=()):
    """Resumo para a mensagem da tela: confirmadas, na fila e recusadas."""
    confirmed = [e.session.title for e in enrollments if e.status == Status.CONFIRMED]
    waiting = [
        f'{e.session.title} (posição {e.waitlist_position()})' for e in enrollments if e.status == Status.WAITLISTED
    ]
    parts = []
    if confirmed:
        parts.append('Sessões confirmadas: ' + ', '.join(confirmed) + '.')
    if waiting:
        parts.append('Na lista de espera: ' + ', '.join(waiting) + '.')
    parts += list(refused)
    return ' '.join(parts)


def session_registrations(session, queryset=None):
    """Inscrições que podem ter presença na sessão: na atividade por sessão, só quem está confirmado nela."""
    queryset = (
        Registration.objects.filter(activity_id=session.activity_id, status=Registration.Status.CONFIRMED)
        if queryset is None
        else queryset
    )
    if session.activity.per_session:
        # Vaga oferecida e ainda não confirmada não vale para presença.
        queryset = queryset.filter(
            session_enrollments__session=session,
            session_enrollments__status=Status.CONFIRMED,
            session_enrollments__offer_expires_at__isnull=True,
        )
    return queryset


def clashes(session):
    """Quantas pessoas desta sessão têm outra sessão escolhida que agora acontece no mesmo horário."""
    registrations = session.enrollments.filter(status__in=ACTIVE).values_list('registration_id', flat=True)
    others = (
        SessionEnrollment.objects.filter(registration_id__in=list(registrations), status__in=ACTIVE)
        .exclude(session=session)
        .select_related('session')
    )
    return len({e.registration_id for e in others if e.session.overlaps(session)})
