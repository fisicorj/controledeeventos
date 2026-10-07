from types import SimpleNamespace

from registrations.models import Registration

from .models import Attendance


def participation(activity, statuses=(Registration.Status.CONFIRMED,)):
    """Frequência de cada inscrito, em consultas fixas (não importa o número de inscritos).

    Retorna (sessões, linhas). Cada linha tem: registration, user, marks (presença em cada sessão,
    na ordem das sessões), count (presenças) e percent (frequência arredondada).
    """
    sessions = list(activity.sessions.all())
    registrations = (
        activity.registrations.filter(status__in=statuses)
        .select_related('user')
        .order_by('user__first_name', 'user__last_name', 'user__username')
    )
    present = set(Attendance.objects.filter(session__activity=activity).values_list('registration_id', 'session_id'))
    rows = []
    for registration in registrations:
        marks = [(registration.id, session.id) in present for session in sessions]
        count = sum(marks)
        rows.append(
            SimpleNamespace(
                registration=registration,
                user=registration.user,
                marks=marks,
                count=count,
                percent=round(count * 100 / len(sessions)) if sessions else 0,
            )
        )
    return sessions, rows
