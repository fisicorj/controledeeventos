from django.db import migrations, models
import django.db.models.deletion
class Migration(migrations.Migration):
    dependencies=[('activities','0001_initial')]
    operations=[migrations.CreateModel(name='Session',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('title',models.CharField(max_length=160)),('start_at',models.DateTimeField()),('end_at',models.DateTimeField()),('location',models.CharField(blank=True,max_length=200)),('check_in_open',models.BooleanField(default=False)),('created_at',models.DateTimeField(auto_now_add=True)),('activity',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='sessions',to='activities.activity'))],options={'ordering':['start_at']})]
