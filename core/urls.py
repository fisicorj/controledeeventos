from django.urls import path

from . import views

urlpatterns = [
    path('', views.settings_edit, name='site_settings'),
    path('certificado-exemplo.pdf', views.certificate_preview, name='certificate_preview'),
    path('email/', views.email_settings, name='email_settings'),
    path('email/teste/', views.email_test, name='email_test'),
]
