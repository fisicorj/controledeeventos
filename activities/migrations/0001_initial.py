from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='Activity', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('title', models.CharField(max_length=200)), ('description', models.TextField()),
        ('activity_type', models.CharField(choices=[('COURSE','Curso'),('EVENT','Evento'),('APPOINTMENT','Agenda')], max_length=20)),
        ('start_date', models.DateTimeField()), ('end_date', models.DateTimeField()),
        ('registration_start', models.DateTimeField()), ('registration_end', models.DateTimeField()),
        ('capacity', models.PositiveIntegerField()), ('location', models.CharField(blank=True, max_length=200)),
        ('image', models.ImageField(blank=True, null=True, upload_to='activities/')),
        ('status', models.CharField(choices=[('DRAFT','Rascunho'),('PUBLISHED','Publicado'),('CANCELLED','Cancelado'),('FINISHED','Finalizado')], default='DRAFT', max_length=20)),
        ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
        ('organizer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='organized_activities', to=settings.AUTH_USER_MODEL)),
    ])]
