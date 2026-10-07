from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = [('registrations','0001_initial'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='Attendance', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('check_in', models.DateTimeField(auto_now_add=True)),
        ('method', models.CharField(choices=[('QR_CODE','QR Code'),('MANUAL','Manual')], default='QR_CODE', max_length=20)),
        ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
        ('registration', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attendances', to='registrations.registration')),
        ('validated_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='validated_attendances', to=settings.AUTH_USER_MODEL)),
    ])]
