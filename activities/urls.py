from django.urls import path

from . import reports, views

urlpatterns = [
    path('', views.activity_list, name='activity_list'),
    path('organizer/', views.organizer_dashboard, name='organizer_dashboard'),
    path('organizer/new/', views.activity_create, name='activity_create'),
    path('organizer/<int:pk>/manage/', views.activity_manage, name='activity_manage'),
    path('organizer/<int:pk>/edit/', views.activity_edit, name='activity_edit'),
    path('organizer/<int:pk>/delete/', views.activity_delete, name='activity_delete'),
    path('organizer/<int:pk>/cancel/', views.activity_cancel, name='activity_cancel'),
    path('organizer/<int:pk>/finish/', views.activity_finish, name='activity_finish'),
    path('organizer/<int:pk>/certificates/', views.certificates_issue, name='certificates_issue'),
    path('organizer/<int:activity_id>/sessions/new/', views.session_create, name='session_create'),
    path('organizer/sessions/<int:pk>/edit/', views.session_edit, name='session_edit'),
    path('organizer/sessions/<int:pk>/delete/', views.session_delete, name='session_delete'),
    path('organizer/<int:activity_id>/questions/new/', views.question_create, name='question_create'),
    path('organizer/questions/<int:pk>/edit/', views.question_edit, name='question_edit'),
    path('organizer/questions/<int:pk>/delete/', views.question_delete, name='question_delete'),
    # Relatórios
    path('organizer/<int:pk>/export.csv', reports.registrations_csv, name='report_registrations_csv'),
    path('organizer/<int:pk>/frequencia.pdf', reports.attendance_pdf, name='report_attendance_pdf'),
    path('organizer/sessions/<int:pk>/assinaturas.pdf', reports.signature_pdf, name='report_signature_pdf'),
    path('<int:pk>/', views.activity_detail, name='activity_detail'),
]
