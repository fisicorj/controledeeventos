from django.contrib.auth import views as auth_views
from django.urls import path

from .mfa_views import mfa_codes, mfa_disable, mfa_new_codes, mfa_setup, mfa_verify, reauth_view
from .views import (
    PortalLoginView,
    PortalLogoutView,
    PortalPasswordChangeView,
    PortalPasswordResetConfirmView,
    PortalPasswordResetView,
    delete_account,
    my_data,
    profile,
    signup,
    verify_email,
)

urlpatterns = [
    path('signup/', signup, name='signup'),
    path('login/', PortalLoginView.as_view(), name='login'),
    path('logout/', PortalLogoutView.as_view(), name='logout'),
    path('meus-dados/', profile, name='profile'),
    path('confirmar-email/', verify_email, name='verify_email'),
    path('meus-dados/copia.json', my_data, name='my_data'),
    path('meus-dados/excluir-conta/', delete_account, name='delete_account'),
    path('meus-dados/senha/', PortalPasswordChangeView.as_view(), name='password_change'),
    # Verificação em duas etapas (administradores e organizadores)
    path('verificacao/', mfa_verify, name='mfa_verify'),
    path('confirmar-identidade/', reauth_view, name='reauth'),
    path('meus-dados/duas-etapas/', mfa_setup, name='mfa_setup'),
    path('meus-dados/duas-etapas/codigos/', mfa_codes, name='mfa_codes'),
    path('meus-dados/duas-etapas/novos-codigos/', mfa_new_codes, name='mfa_new_codes'),
    path('meus-dados/duas-etapas/desativar/', mfa_disable, name='mfa_disable'),
    # Recuperação de senha (o e-mail sai pelo servidor configurado em Configurações > E-mail)
    path(
        'password-reset/',
        PortalPasswordResetView.as_view(
            template_name='accounts/password_reset_form.html',
            email_template_name='accounts/password_reset_email.txt',
            subject_template_name='accounts/password_reset_subject.txt',
        ),
        name='password_reset',
    ),
    path(
        'password-reset/sent/',
        auth_views.PasswordResetDoneView.as_view(template_name='accounts/password_reset_done.html'),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        PortalPasswordResetConfirmView.as_view(),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(template_name='accounts/password_reset_complete.html'),
        name='password_reset_complete',
    ),
]
