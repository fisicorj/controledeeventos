from django.urls import path

from . import audit_views, batch_views, views

urlpatterns = [
    path('validar/', views.verify, name='certificate_verify'),
    path('validar/<str:code>/', views.verify_code, name='certificate_verify_code'),
    path('<str:code>/pdf/', views.download, name='certificate_download'),
    path('auditoria/', audit_views.audit_list, name='certificate_audit'),
    path('auditoria.csv', audit_views.audit_csv, name='certificate_audit_csv'),
    # Emissão avulsa (evento que não foi cadastrado no sistema)
    path('avulsos/', batch_views.batch_list, name='batch_list'),
    path('avulsos/novo/', batch_views.batch_new, name='batch_new'),
    path('avulsos/previa/', batch_views.batch_preview, name='batch_preview'),
    path('avulsos/modelo.xlsx', batch_views.batch_template, name='batch_template'),
    path('avulsos/<int:pk>/', batch_views.batch_detail, name='batch_detail'),
    path('avulsos/<int:pk>/adicionar/', batch_views.batch_add, name='batch_add'),
    path('avulsos/<int:pk>/editar/', batch_views.batch_edit, name='batch_edit'),
    path('avulsos/<int:pk>/cancelar/', batch_views.batch_cancel, name='batch_cancel'),
    path('avulsos/<int:pk>/zip/', batch_views.batch_zip, name='batch_zip'),
    path('avulsos/<int:pk>/csv/', batch_views.batch_csv, name='batch_csv'),
    path('avulsos/<int:pk>/email/', batch_views.batch_email, name='batch_email'),
    path(
        'avulsos/<int:pk>/certificado/<str:code>/revogar/',
        batch_views.batch_certificate_revoke,
        name='batch_certificate_revoke',
    ),
]
