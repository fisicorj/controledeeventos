from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('attendance', '0003_fill_attendance_session')]
    operations = [
        migrations.AlterField(
            model_name='attendance', name='session',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attendances', to='activities.session'),
        ),
    ]
