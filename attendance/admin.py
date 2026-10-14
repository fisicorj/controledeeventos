from django.contrib import admin

from core.http import client_ip

from .audit import record_removals
from .models import Attendance, AttendanceRemoval


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('registration', 'session', 'check_in', 'validated_by', 'method')
    list_filter = ('method',)

    # Remoções pelo painel também ficam no registro permanente.
    def delete_model(self, request, obj):
        record_removals([obj], actor=request.user, ip=client_ip(request), origin='Painel administrativo')
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        attendances = queryset.select_related('session__activity', 'registration__user', 'validated_by')
        record_removals(attendances, actor=request.user, ip=client_ip(request), origin='Painel administrativo')
        super().delete_queryset(request, queryset)


@admin.register(AttendanceRemoval)
class AttendanceRemovalAdmin(admin.ModelAdmin):
    """Somente leitura: o banco também recusa alteração e exclusão."""

    list_display = ('created_at', 'participant_name', 'session_label', 'activity_title', 'actor_name', 'origin')
    list_filter = ('origin',)
    search_fields = ('participant_name', 'activity_title', 'actor_name')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
