from django.contrib import admin

from .models import Registration, SessionEnrollment


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'activity', 'status', 'created_at')
    list_filter = ('status',)


@admin.register(SessionEnrollment)
class SessionEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('registration', 'session', 'status', 'created_at')
    list_filter = ('status',)
    raw_id_fields = ('registration', 'session')
