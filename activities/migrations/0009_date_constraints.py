from django.db import migrations, models

from core.migration_utils import check_constraint_not_valid


def _activities(condition):
    def describe(Activity):
        return [f'atividade {pk}: {title}' for pk, title in Activity.objects.filter(condition).values_list('pk', 'title')]

    return describe


def _sessions(Session):
    rows = Session.objects.filter(end_at__lte=models.F('start_at')).values_list('pk', 'title')
    return [f'sessão {pk}: {title}' for pk, title in rows]


class Migration(migrations.Migration):
    dependencies = [
        ('activities', '0008_waitlist_offer'),
    ]

    operations = [
        check_constraint_not_valid(
            'activities',
            'activity',
            models.CheckConstraint(condition=models.Q(end_date__gt=models.F('start_date')), name='activity_end_after_start'),
            _activities(models.Q(end_date__lte=models.F('start_date'))),
        ),
        check_constraint_not_valid(
            'activities',
            'activity',
            models.CheckConstraint(
                condition=models.Q(registration_end__gt=models.F('registration_start')),
                name='activity_registration_period',
            ),
            _activities(models.Q(registration_end__lte=models.F('registration_start'))),
        ),
        check_constraint_not_valid(
            'activities',
            'activity',
            models.CheckConstraint(
                condition=models.Q(registration_end__lte=models.F('end_date')), name='activity_registration_until_end'
            ),
            _activities(models.Q(registration_end__gt=models.F('end_date'))),
        ),
        check_constraint_not_valid(
            'activities',
            'session',
            models.CheckConstraint(condition=models.Q(end_at__gt=models.F('start_at')), name='session_end_after_start'),
            _sessions,
        ),
    ]
