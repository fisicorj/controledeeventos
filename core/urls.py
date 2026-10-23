from django.urls import path

from accounts import user_admin_views as users

from . import views

urlpatterns = [
    path('', views.settings_edit, name='site_settings'),
    path('certificado-exemplo.pdf', views.certificate_preview, name='certificate_preview'),
    path('imagem/<str:field>/', views.private_image, name='settings_private_image'),
    path('email/', views.email_settings, name='email_settings'),
    path('email/teste/', views.email_test, name='email_test'),
    path('email/fila/tentar-de-novo/', views.outbox_retry, name='outbox_retry'),
    path('usuarios/', users.user_list, name='user_admin_list'),
    path('usuarios/usuarios.csv', users.user_list_csv, name='user_admin_csv'),
    path('usuarios/convites/', users.invite_list, name='user_admin_invites'),
    path('usuarios/convites/<int:pk>/cancelar/', users.invite_cancel, name='user_admin_invite_cancel'),
    path('usuarios/convites/<int:pk>/reenviar/', users.invite_resend, name='user_admin_invite_resend'),
    path('usuarios/<int:pk>/', users.user_detail, name='user_admin_detail'),
    path('usuarios/<int:pk>/transferir/', users.user_transfer, name='user_admin_transfer'),
    path('usuarios/<int:pk>/papel/', users.user_role, name='user_admin_role'),
    path('usuarios/<int:pk>/ativa/', users.user_active, name='user_admin_active'),
    path('usuarios/<int:pk>/duas-etapas/', users.user_mfa_reset, name='user_admin_mfa_reset'),
    path('usuarios/<int:pk>/nova-senha/', users.user_password_link, name='user_admin_password_link'),
    path('usuarios/<int:pk>/codigo/', users.user_resend_code, name='user_admin_resend_code'),
    path('usuarios/<int:pk>/excluir/', users.user_delete, name='user_admin_delete'),
    path('manutencao/', views.maintenance_status, name='maintenance_status'),
    path('manutencao/rodar/', views.maintenance_run, name='maintenance_run'),
]
