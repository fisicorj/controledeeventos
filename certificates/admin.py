from django.contrib import admin

from .models import Certificate, CertificateBatch, CertificateEvent


class ReadOnlyAdmin(admin.ModelAdmin):
    """Só consulta: emitir, alterar e revogar passam pelas telas do sistema, que registram a auditoria."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Certificate)
class CertificateAdmin(ReadOnlyAdmin):
    list_display = ('code', 'participant_name', 'participant_document', 'activity_title', 'issued_at', 'revoked_at')
    list_filter = ('revoked_at',)
    search_fields = ('code', 'participant_name', 'participant_document', 'activity_title')


@admin.register(CertificateBatch)
class CertificateBatchAdmin(ReadOnlyAdmin):
    list_display = ('title', 'start_date', 'end_date', 'created_by', 'created_at', 'cancelled_at')
    search_fields = ('title',)


@admin.register(CertificateEvent)
class CertificateEventAdmin(ReadOnlyAdmin):
    list_display = ('created_at', 'action', 'code', 'participant_name', 'activity_title', 'actor_name', 'ip')
    list_filter = ('action',)
    search_fields = ('code', 'participant_name', 'participant_document', 'activity_title', 'actor_name')
    date_hierarchy = 'created_at'
