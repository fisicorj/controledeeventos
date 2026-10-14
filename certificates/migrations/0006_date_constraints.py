from django.db import migrations, models

from core.migration_utils import check_constraint_not_valid


def _batches(condition):
    def describe(Batch):
        return [f'emissão {pk}: {title}' for pk, title in Batch.objects.filter(condition).values_list('pk', 'title')]

    return describe


class Migration(migrations.Migration):
    dependencies = [
        ('certificates', '0005_audit'),
    ]

    operations = [
        check_constraint_not_valid(
            'certificates',
            'certificatebatch',
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F('start_date')), name='batch_end_not_before_start'
            ),
            _batches(models.Q(end_date__lt=models.F('start_date'))),
        ),
        check_constraint_not_valid(
            'certificates',
            'certificatebatch',
            models.CheckConstraint(
                condition=models.Q(issue_date__gte=models.F('start_date')), name='batch_issue_not_before_start'
            ),
            _batches(models.Q(issue_date__lt=models.F('start_date'))),
        ),
    ]
