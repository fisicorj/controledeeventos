"""Registro das presenças removidas (tabela AttendanceRemoval, só de inclusão)."""

from django.utils import timezone

from .models import AttendanceRemoval


def person_name(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return ''
    full = user.get_full_name()
    return f'{full} ({user.username})' if full else user.username


def session_label(session):
    when = timezone.localtime(session.start_at).strftime('%d/%m/%Y %H:%M') if session.start_at else ''
    return ' - '.join(part for part in (session.title, when) if part)[:300]


def record_removals(attendances, *, actor=None, ip=None, origin='Lista de presença'):
    """Grava uma linha para cada presença, antes de ela ser apagada."""
    rows = []
    for attendance in attendances:
        session, registration = attendance.session, attendance.registration
        rows.append(
            AttendanceRemoval(
                activity_id=session.activity_id,
                activity_title=session.activity.title,
                session_id=session.pk,
                session_label=session_label(session),
                registration_id=registration.pk,
                participant_name=person_name(registration.user),
                check_in=attendance.check_in,
                method=attendance.method,
                validated_by_name=person_name(attendance.validated_by),
                actor_id=actor.pk if actor is not None and actor.is_authenticated else None,
                actor_name=person_name(actor),
                ip=ip or None,
                origin=origin,
            )
        )
    return AttendanceRemoval.objects.bulk_create(rows)
