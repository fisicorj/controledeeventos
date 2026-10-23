from django.urls import path

from . import survey
from .views import (
    cancel_registration,
    confirm_offer,
    my_calendar,
    my_registrations,
    qr_code,
    register_activity,
    registration_sessions,
)

urlpatterns = [
    path('mine/', my_registrations, name='my_registrations'),
    path('activity/<int:activity_id>/register/', register_activity, name='register_activity'),
    path('<int:pk>/cancel/', cancel_registration, name='cancel_registration'),
    path('<int:pk>/confirmar-vaga/', confirm_offer, name='confirm_offer'),
    path('<int:pk>/qr/', qr_code, name='registration_qr'),
    path('<int:pk>/agenda.ics', my_calendar, name='registration_calendar'),
    path('<int:pk>/sessoes/', registration_sessions, name='registration_sessions'),
    path('<int:pk>/avaliacao/', survey.survey, name='survey'),
    path('atividade/<int:activity_id>/avaliacoes.csv', survey.survey_csv, name='survey_csv'),
    path('atividade/<int:activity_id>/avaliacoes/lembrete/', survey.survey_remind, name='survey_remind'),
]
