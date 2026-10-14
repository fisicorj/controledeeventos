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
def finish_activity(activity, request=None, check_status=True):
    """Encerra a atividade: fecha o check-in de todas as sessões e emite os certificados.

    Trava a atividade (SELECT ... FOR UPDATE): dois cliques ou duas pessoas ao mesmo tempo não emitem
    nada em dobro. Devolve None se ela já não estava publicada (alguém finalizou ou cancelou antes)."""
    from registrations.services import clear_offers, lock_activity

    locked = lock_activity(activity.pk)
    if check_status and locked.status != Activity.Status.PUBLISHED:
        return None
    activity.status = Activity.Status.FINISHED
    activity.save(update_fields=['status', 'updated_at'])
    activity.sessions.update(check_in_open=False)
    clear_offers(activity)
    issued = issue_certificates(activity, request=request)
    if activity.survey_enabled:
        # Quem recebeu certificado já ganha o convite da pesquisa no mesmo e-mail.
        from registrations.survey import eligible

        certified = {c.registration_id for c in issued}
        others = [r for r in eligible(activity).select_related('user', 'activity') if r.id not in certified]
        notifications.notify_survey(others)
    return issued
