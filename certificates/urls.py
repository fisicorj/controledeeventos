from django.urls import path

from . import audit_views, batch_views, manage_views, views

urlpatterns = [
    path('validar/', views.verify, name='certificate_verify'),
    path('validar-varios/', views.verify_bulk, name='certificate_verify_bulk'),
    path('minhas-horas/', views.my_hours, name='my_hours'),
    path('minhas-horas/vincular/', views.claim_start, name='certificate_claim_start'),
    path('minhas-horas/vincular/<str:code>/', views.claim_confirm, name='certificate_claim'),
    path('minhas-horas/desvincular/<str:code>/', views.claim_undo, name='certificate_claim_undo'),
    path('validar/<str:code>/', views.verify_code, name='certificate_verify_code'),
    path('declaracao/<str:code>/', views.declaration_verify, name='declaration_verify'),
    path('minhas-horas/declaracao/<str:code>/cancelar/', views.declaration_cancel, name='declaration_cancel'),
    path('<str:code>/pdf/', views.download, name='certificate_download'),
    path('<str:code>/revogar/', manage_views.certificate_revoke, name='certificate_revoke'),
    path('<str:code>/corrigir/', manage_views.certificate_correct, name='certificate_correct'),
    path('auditoria/', audit_views.audit_list, name='certificate_audit'),
    path('auditoria.csv', audit_views.audit_csv, name='certificate_audit_csv'),
    # Emissão avulsa (evento que não foi cadastrado no sistema)
    path('avulsos/', batch_views.batch_list, name='batch_list'),
    path('avulsos/turmas/', batch_views.group_report, name='batch_group_report'),
    path('avulsos/novo/', batch_views.batch_new, name='batch_new'),
    path('avulsos/novo/planilha-completa/', batch_views.batch_full, name='batch_full'),
    path('avulsos/previa/', batch_views.batch_preview, name='batch_preview'),
    path('avulsos/modelo.xlsx', batch_views.batch_template, name='batch_template'),
    path('avulsos/<int:pk>/', batch_views.batch_detail, name='batch_detail'),
    path('avulsos/<int:pk>/adicionar/', batch_views.batch_add, name='batch_add'),
    path('avulsos/<int:pk>/editar/', batch_views.batch_edit, name='batch_edit'),
    path('avulsos/<int:pk>/cancelar/', batch_views.batch_cancel, name='batch_cancel'),
    path('avulsos/<int:pk>/zip/', batch_views.batch_zip, name='batch_zip'),
    path('avulsos/<int:pk>/zip/gerar/', batch_views.batch_zip_request, name='batch_zip_request'),
    path('avulsos/<int:pk>/zip/<int:export_pk>/', batch_views.batch_zip_file, name='batch_zip_file'),
    path('avulsos/<int:pk>/csv/', batch_views.batch_csv, name='batch_csv'),
    path('avulsos/<int:pk>/email/', batch_views.batch_email, name='batch_email'),
    path(
        'avulsos/<int:pk>/certificado/<str:code>/revogar/',
        batch_views.batch_certificate_revoke,
        name='batch_certificate_revoke',
    ),
]
