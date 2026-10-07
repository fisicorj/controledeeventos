import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = [('activities','0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='Registration', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('status', models.CharField(choices=[('CONFIRMED','Confirmada'),('CANCELLED','Cancelada')], default='CONFIRMED', max_length=20)),
            ('qr_token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('activity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='registrations', to='activities.activity')),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='registrations', to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.AddConstraint(model_name='registration', constraint=models.UniqueConstraint(fields=('user','activity'), name='unique_activity_registration')),
    ]
