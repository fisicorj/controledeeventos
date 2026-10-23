"""Ações da organização nas inscrições: incluir um participante, cancelar uma inscrição e colocar ou tirar alguém
de uma sessão. Valem as mesmas travas e filas das ações do próprio participante (services e sessions); a diferença
é que a organização pode passar das vagas e agir depois do início, sempre com o motivo registrado e e-mail à pessoa.
"""

from django.db import transaction
from django.utils import timezone

from activities.models import Activity

from . import notifications, services, sessions
from .models import Registration, RegistrationLog, SessionEnrollment

Log = RegistrationLog.Action


class Refused(Exception):
    """A ação não pode ser feita; a mensagem explica por quê."""


def has_certificates(registration):
    from certificates.models import Certificate

    valid = Certificate.objects.filter(revoked_at__isnull=True)
    return (
        valid.filter(registration=registration).exists()
        or valid.filter(session_enrollment__registration=registration).exists()
    )


@transaction.atomic
def add_participant(activity, user, actor, category=None, note=''):
    """Inscreve (ou confirma) a pessoa, mesmo com o evento lotado. Devolve (inscrição, estava acima das vagas)."""
    activity = services.lock_activity(activity.pk)
    if activity.status != Activity.Status.PUBLISHED:
        raise Refused('Só dá para incluir participantes numa atividade publicada.')
    if not user.is_active:
        raise Refused('Esta conta está desativada.')
    if not user.email_verified:
        # Sem e-mail confirmado, o QR Code e o certificado iriam para um endereço que ninguém conferiu (V7-04).
        raise Refused(
            f'A conta {user.username} ainda não confirmou o e-mail. Peça à pessoa para entrar no portal e '
            'digitar o código que recebeu (ou pedir um novo); depois, tente de novo.'
        )
    registration = Registration.objects.filter(user=user, activity=activity).first()
    if registration and registration.status == Registration.Status.CONFIRMED and not registration.offer_pending:
        raise Refused(f'{user.get_full_name() or user.username} já está inscrito nesta atividade.')
    over = not services.has_room(activity, category)
    if registration is None:
        registration = Registration(user=user, activity=activity)
    registration.status = Registration.Status.CONFIRMED
    registration.category = category
    registration.waitlisted_at = None
    registration.cancelled_at = None
    registration.offer_expires_at = None
    registration.reminder_sent_at = None
    registration.save()
    RegistrationLog.objects.create(registration=registration, action=Log.ADDED, note=note.strip(), actor=actor)
    notifications.notify_registration_confirmed(registration)
    return registration, over


@transaction.atomic
def cancel(registration, actor, note):
    """Cancela a inscrição: sai das sessões futuras e a vaga vai para a fila. Não vale com certificado emitido."""
    activity = services.lock_activity(registration.activity_id)
    registration.refresh_from_db()
    if registration.status == Registration.Status.CANCELLED:
        raise Refused('Esta inscrição já estava cancelada.')
    if has_certificates(registration):
        raise Refused('A pessoa já tem certificado desta atividade. Revogue o certificado antes de cancelar.')
    was_confirmed = registration.status == Registration.Status.CONFIRMED
    registration.status = Registration.Status.CANCELLED
    registration.waitlisted_at = None
    registration.offer_expires_at = None
    registration.cancelled_at = timezone.now()
    registration.save(update_fields=['status', 'waitlisted_at', 'offer_expires_at', 'cancelled_at'])
    sessions.cancel_all(registration)
    RegistrationLog.objects.create(registration=registration, action=Log.CANCELLED, note=note.strip(), actor=actor)
    notifications.notify_cancelled_by_staff(registration, note.strip())
    if was_confirmed and activity.status == Activity.Status.PUBLISHED:
        services.promote_waitlist(activity)


@transaction.atomic
def add_to_session(registration, session, actor, note=''):
    """Coloca a pessoa confirmada na sessão, mesmo lotada ou já começada (quem chegou na hora, troca combinada).
    Devolve (inscrição na sessão, estava acima das vagas)."""
    services.lock_activity(registration.activity_id)
    registration.refresh_from_db()
    if session.activity_id != registration.activity_id:
        raise Refused('Esta sessão é de outra atividade.')
    if registration.status != Registration.Status.CONFIRMED:
        raise Refused('A inscrição no evento precisa estar confirmada.')
    enrollment, _ = SessionEnrollment.objects.get_or_create(
        registration=registration, session=session, defaults={'status': SessionEnrollment.Status.CANCELLED}
    )
    if enrollment.status == SessionEnrollment.Status.CONFIRMED and not enrollment.offer_pending:
        raise Refused(f'A pessoa já está confirmada em "{session.title}".')
    taken = session.enrollments.filter(status=SessionEnrollment.Status.CONFIRMED).exclude(pk=enrollment.pk).count()
    over = not sessions.has_seat(session, taken)
    enrollment.status = SessionEnrollment.Status.CONFIRMED
    enrollment.waitlisted_at = None
    enrollment.cancelled_at = None
    enrollment.offer_expires_at = None
    enrollment.save()
    RegistrationLog.objects.create(
        registration=registration, action=Log.SESSION_ADDED, session_title=session.title, note=note.strip(),
        actor=actor,
    )  # fmt: skip
    notifications.notify_session_changed_by_staff(enrollment, added=True, note=note.strip())
    return enrollment, over


@transaction.atomic
def remove_from_session(enrollment, actor, note=''):
    """Tira a pessoa da sessão (ou da fila dela). A vaga vai para a fila, se a sessão ainda não começou."""
    services.lock_activity(enrollment.session.activity_id)
    enrollment.refresh_from_db()
    if not enrollment.is_active:
        raise Refused('A pessoa já não estava nesta sessão.')
    from certificates.models import Certificate

    if Certificate.objects.filter(session_enrollment=enrollment, revoked_at__isnull=True).exists():
        raise Refused('A pessoa já tem o certificado desta sessão. Revogue o certificado antes.')
    was_confirmed = enrollment.status == SessionEnrollment.Status.CONFIRMED
    enrollment.status = SessionEnrollment.Status.CANCELLED
    enrollment.waitlisted_at = None
    enrollment.offer_expires_at = None
    enrollment.cancelled_at = timezone.now()
    enrollment.save(update_fields=['status', 'waitlisted_at', 'offer_expires_at', 'cancelled_at'])
    RegistrationLog.objects.create(
        registration=enrollment.registration, action=Log.SESSION_REMOVED, session_title=enrollment.session.title,
        note=note.strip(), actor=actor,
    )  # fmt: skip
    notifications.notify_session_changed_by_staff(enrollment, added=False, note=note.strip())
    if was_confirmed:
        sessions.promote(enrollment.session)


WALK_IN_NOTE = 'Inscrição na hora, pelo check-in.'


@transaction.atomic
def walk_in(session, user, actor, category=None, can_exceed=False):
    """Quem chegou sem inscrição (M-06): inscreve no evento (e na sessão, no modo por sessão) e já registra a
    presença. A equipe de check-in só inscreve havendo vaga; a organização (can_exceed) pode passar das vagas.
    Devolve (inscrição, ficou acima das vagas)."""
    from attendance.models import Attendance

    activity = services.lock_activity(session.activity_id)
    if activity.status != Activity.Status.PUBLISHED:
        raise Refused('Só dá para inscrever na hora numa atividade publicada.')
    name = user.get_full_name() or user.username
    registration = Registration.objects.filter(user=user, activity=activity).first()
    registered = (
        registration is not None
        and registration.status == Registration.Status.CONFIRMED
        and not registration.offer_pending
    )
    in_session = (
        not activity.per_session
        or registered
        and registration.session_enrollments.filter(
            session=session, status=SessionEnrollment.Status.CONFIRMED, offer_expires_at__isnull=True
        ).exists()
    )
    if registered and in_session:
        raise Refused(f'{name} já está inscrito: registre a presença na lista.')
    over = False
    if not registered:
        if not can_exceed and not services.has_room(activity, category):
            raise Refused('Não há vaga no evento. Só a organização pode inscrever acima das vagas.')
        registration, over = add_participant(activity, user, actor, category, WALK_IN_NOTE)
    if activity.per_session:
        taken = session.enrollments.filter(status=SessionEnrollment.Status.CONFIRMED).count()
        if not can_exceed and not sessions.has_seat(session, taken):
            raise Refused(f'"{session.title}" está lotada. Só a organização pode colocar acima das vagas.')
        _, session_over = add_to_session(registration, session, actor, WALK_IN_NOTE)
        over = over or session_over
    Attendance.objects.get_or_create(
        registration=registration,
        session=session,
        defaults={'validated_by': actor, 'method': Attendance.Method.MANUAL},
    )
    return registration, over
