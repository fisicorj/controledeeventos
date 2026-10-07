from django.db import transaction

from activities.models import Activity
from attendance.summary import participation
from registrations import notifications

from . import audit
from .models import Certificate


def issue_certificates(activity, request=None):
    """Emite certificado para quem atingiu a frequência mínima e ainda não tem um.

    Pode ser chamada de novo sem risco (não duplica nem revoga). Retorna os certificados novos.
    """
    if not activity.certificate_enabled:
        return []
    sessions, rows = participation(activity)
    if not sessions:
        return []
    already = set(Certificate.objects.filter(registration__activity=activity).values_list('registration_id', flat=True))
    issued = []
    for row in rows:
        if row.registration.id in already or row.percent < activity.min_attendance_percent:
            continue
        issued.append(
            Certificate.objects.create(
                registration=row.registration,
                participant_name=row.user.get_full_name() or row.user.username,
                participant_document=row.user.institutional_id,
                activity_title=activity.title,
                activity_type=activity.get_activity_type_display(),
                start_date=activity.start_date,
                end_date=activity.end_date,
                workload_hours=activity.workload_hours,
                attendance_percent=row.percent,
            )
        )
    audit.record(audit.Action.ISSUED, issued, request=request, details={'source': 'Atividade do sistema'})
    notifications.notify_certificates(issued)
    return issued


@transaction.atomic
def finish_activity(activity, request=None):
    """Encerra a atividade: fecha o check-in de todas as sessões e emite os certificados."""
    activity.status = Activity.Status.FINISHED
    activity.save(update_fields=['status', 'updated_at'])
    activity.sessions.update(check_in_open=False)
    return issue_certificates(activity, request=request)
