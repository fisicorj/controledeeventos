from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import MfaDevice, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (('Instituição', {'fields': ('role', 'institutional_id', 'email_verified')}),)
    list_display = ('username', 'email', 'first_name', 'last_name', 'institutional_id', 'email_verified', 'role')


@admin.register(MfaDevice)
class MfaDeviceAdmin(admin.ModelAdmin):
    """Só para consultar e apagar (apagar = a pessoa cadastra o aplicativo de novo no próximo acesso)."""

    list_display = ('user', 'confirmed_at', 'created_at')
    search_fields = ('user__username', 'user__email')
    fields = ('user', 'confirmed_at', 'created_at')
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
