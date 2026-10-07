from django.urls import path

from .views import cancel_registration, my_registrations, qr_code, register_activity

urlpatterns = [
    path('mine/', my_registrations, name='my_registrations'),
    path('activity/<int:activity_id>/register/', register_activity, name='register_activity'),
    path('<int:pk>/cancel/', cancel_registration, name='cancel_registration'),
    path('<int:pk>/qr/', qr_code, name='registration_qr'),
]
