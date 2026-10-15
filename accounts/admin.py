from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import MfaDevice, User, UserChange


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """Uso de emergência. O dia a dia é em Configurações > Usuários, onde valem as regras do sistema (ver
    accounts/user_admin.py). Aqui: sem "Grupos" e "Permissões" (o portal usa só o campo Papel), papel e e-mail
    só para leitura (mudam pelo painel ou pela confirmação por código) e sem apagar contas (o painel anonimiza)."""

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Dados pessoais', {'fields': ('first_name', 'last_name', 'email', 'institutional_id', 'email_verified')}),
        ('Acesso', {'fields': ('role', 'is_active', 'is_staff', 'is_superuser')}),
        ('Datas', {'fields': ('last_login', 'date_joined')}),
    )
    readonly_fields = ('role', 'email', 'email_verified', 'last_login', 'date_joined')
    list_display = ('username', 'email', 'first_name', 'last_name', 'institutional_id', 'email_verified', 'role')
    list_filter = ('role', 'is_active', 'is_staff', 'is_superuser', 'email_verified')

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserChange)
class UserChangeAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'target_name', 'actor_name')
    list_filter = ('action',)
    search_fields = ('target__username', 'target__first_name', 'target__last_name', 'actor_name')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
