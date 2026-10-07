from django.contrib import admin

from .models import Attendance


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('registration', 'session', 'check_in', 'validated_by', 'method')
    list_filter = ('method',)
