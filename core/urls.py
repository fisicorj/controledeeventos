from django.urls import path

from . import views

urlpatterns = [
    path('', views.settings_edit, name='site_settings'),
    path('certificado-exemplo.pdf', views.certificate_preview, name='certificate_preview'),
    path('imagem/<str:field>/', views.private_image, name='settings_private_image'),
    path('email/', views.email_settings, name='email_settings'),
    path('email/teste/', views.email_test, name='email_test'),
    path('email/fila/tentar-de-novo/', views.outbox_retry, name='outbox_retry'),
    path('manutencao/', views.maintenance_status, name='maintenance_status'),
    path('manutencao/rodar/', views.maintenance_run, name='maintenance_run'),
]
