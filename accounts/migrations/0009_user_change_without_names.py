from django.db import migrations

PERSONAL_KEYS = ('email', 'conta')


def redact(apps, schema_editor):
    """Tira do histórico o nome e o e-mail das contas alteradas, gravados antes da SEC-26 (3ª auditoria)."""
    UserChange = apps.get_model('accounts', 'UserChange')
    for change in UserChange.objects.all().iterator():
        details = {k: v for k, v in (change.details or {}).items() if k not in PERSONAL_KEYS}
        target_name = f'conta {change.target_id}' if change.target_id else 'conta removida'
        if details != change.details or target_name != change.target_name:
            change.details, change.target_name = details, target_name
            change.save(update_fields=['details', 'target_name'])


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0008_user_deleted_at'),
    ]

    operations = [
        migrations.RunPython(redact, migrations.RunPython.noop),
    ]
