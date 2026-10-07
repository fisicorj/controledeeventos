from django.contrib import admin

from .models import Activity, Session


class SessionInline(admin.TabularInline):
    model = Session
    extra = 0


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ('title', 'activity_type', 'start_date', 'capacity', 'status', 'organizer')
    list_filter = ('activity_type', 'status')
    search_fields = ('title', 'description')
    inlines = [SessionInline]

    def get_actions(self, request):
        # A exclusão em massa apagaria inscrições e presenças em cascata.
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.registrations.exists():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('title', 'activity', 'start_at', 'check_in_open')
