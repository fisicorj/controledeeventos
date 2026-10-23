from django.urls import path

from . import views

urlpatterns = [
    path('', views.checkin_home, name='checkin_home'),
    path('agora/', views.checkin_quick, name='checkin_quick'),
    path('manifest.webmanifest', views.checkin_manifest, name='checkin_manifest'),
    path('sw.js', views.checkin_service_worker, name='checkin_sw'),
    path('session/<int:session_id>/app/', views.checkin_app, name='checkin_app'),
    path('session/<int:session_id>/roster.json', views.checkin_roster, name='checkin_roster'),
    path('session/<int:session_id>/status.json', views.checkin_status, name='checkin_status'),
    path('session/<int:session_id>/', views.session_attendance, name='session_attendance'),
    path('session/<int:session_id>/manual/<int:registration_id>/', views.manual_checkin, name='manual_checkin'),
    path('session/<int:session_id>/remover/<int:registration_id>/', views.remove_checkin, name='remove_checkin'),
    path('session/<int:session_id>/abrir-fechar/', views.toggle_checkin, name='toggle_checkin'),
    path('session/<int:session_id>/scan/', views.qr_checkin, name='qr_checkin'),
    path('session/<int:session_id>/na-hora/', views.walk_in_checkin, name='walk_in_checkin'),
]
