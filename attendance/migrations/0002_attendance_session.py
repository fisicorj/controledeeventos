from django.db import migrations, models
import django.db.models.deletion
class Migration(migrations.Migration):
    dependencies=[('activities','0002_session'),('attendance','0001_initial')]
    operations=[migrations.AddField(model_name='attendance',name='session',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.CASCADE,related_name='attendances',to='activities.session')),migrations.AddConstraint(model_name='attendance',constraint=models.UniqueConstraint(fields=('registration','session'),name='unique_registration_session_attendance'))]
