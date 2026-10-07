from django.db import migrations


def fill_sessions(apps, schema_editor):
    """Presenças antigas (V0.2) sem sessão são associadas à primeira sessão da atividade.
    Se a atividade não tiver sessão, cria uma "Sessão única (migrada)" com o período da atividade.
    Se o participante já tiver presença nessa sessão, o registro sem sessão é duplicado e é removido."""
    Attendance = apps.get_model('attendance', 'Attendance')
    Session = apps.get_model('activities', 'Session')
    for att in Attendance.objects.filter(session__isnull=True).select_related('registration__activity'):
        activity = att.registration.activity
        session = Session.objects.filter(activity=activity).order_by('start_at').first()
        if session is None:
            session = Session.objects.create(
                activity=activity, title='Sessão única (migrada)',
                start_at=activity.start_date, end_at=activity.end_date,
                location=activity.location, check_in_open=False,
            )
        if Attendance.objects.filter(registration_id=att.registration_id, session=session).exists():
            att.delete()
        else:
            att.session = session
            att.save(update_fields=['session'])


class Migration(migrations.Migration):
    dependencies = [('attendance', '0002_attendance_session'), ('activities', '0002_session')]
    operations = [migrations.RunPython(fill_sessions, migrations.RunPython.noop)]
