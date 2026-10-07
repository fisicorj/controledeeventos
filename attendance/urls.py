from django.urls import path

from . import views

urlpatterns = [
    path('session/<int:session_id>/', views.session_attendance, name='session_attendance'),
    path('session/<int:session_id>/manual/<int:registration_id>/', views.manual_checkin, name='manual_checkin'),
    path('session/<int:session_id>/scan/', views.qr_checkin, name='qr_checkin'),
]
