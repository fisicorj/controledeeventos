from django.contrib import admin

from .models import OutgoingEmail, SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    list_display = ('institution_name', 'city', 'signer_name', 'updated_at')

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OutgoingEmail)
class OutgoingEmailAdmin(admin.ModelAdmin):
    """Só consulta: a fila é alimentada pelo sistema (ver core/outbox.py)."""

    list_display = ('created_at', 'to', 'subject', 'status', 'attempts', 'sent_at')
    list_filter = ('status',)
    search_fields = ('to', 'subject', 'group')
    readonly_fields = [f.name for f in OutgoingEmail._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
