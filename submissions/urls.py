from django.urls import path

from . import views

urlpatterns = [
    path('meus/', views.my_submissions, name='my_submissions'),
    path('avaliar/', views.my_reviews, name='my_reviews'),
    path('avaliacao/<int:pk>/', views.review_form, name='review_form'),
    path('evento/<slug:slug>/', views.call_page, name='call_page'),
    path('evento/<slug:slug>/enviar/', views.submission_create, name='submission_create'),
    path('<int:pk>/', views.submission_detail, name='submission_detail'),
    path('<int:pk>/editar/', views.submission_edit, name='submission_edit'),
    path('<int:pk>/retirar/', views.submission_withdraw, name='submission_withdraw'),
    path('<int:pk>/arquivo/', views.submission_file, name='submission_file'),
    path('organizacao/<int:activity_id>/', views.call_manage, name='call_manage'),
    path('organizacao/<int:activity_id>/chamada/', views.call_edit, name='call_edit'),
    path('organizacao/<int:activity_id>/avaliadores/', views.reviewer_add, name='reviewer_add'),
    path(
        'organizacao/<int:activity_id>/avaliadores/<int:user_id>/remover/',
        views.reviewer_remove,
        name='reviewer_remove',
    ),
    path(
        'organizacao/<int:activity_id>/avaliadores/<int:user_id>/lembrar/',
        views.reviewer_remind,
        name='reviewer_remind',
    ),
    path(
        'organizacao/<int:activity_id>/avaliadores/<int:user_id>/areas/',
        views.reviewer_set_areas,
        name='reviewer_set_areas',
    ),
    path('organizacao/<int:activity_id>/distribuir/', views.call_auto_assign, name='call_auto_assign'),
    path('organizacao/<int:activity_id>/avaliadores/copiar/', views.reviewers_copy, name='reviewers_copy'),
    path(
        'organizacao/<int:activity_id>/convites/<int:invite_id>/cancelar/',
        views.reviewer_invite_cancel,
        name='reviewer_invite_cancel',
    ),
    path(
        'organizacao/<int:activity_id>/convites/<int:invite_id>/reenviar/',
        views.reviewer_invite_resend,
        name='reviewer_invite_resend',
    ),
    path('organizacao/<int:activity_id>/coordenacao/', views.coordinator_add, name='coordinator_add'),
    path(
        'organizacao/<int:activity_id>/coordenacao/<int:user_id>/remover/',
        views.coordinator_remove,
        name='coordinator_remove',
    ),
    path('convite-avaliador/<str:token>/', views.reviewer_invite_accept, name='reviewer_invite_accept'),
    path('organizacao/<int:activity_id>/trabalhos.csv', views.submissions_csv, name='submissions_csv'),
    path('organizacao/trabalho/<int:pk>/', views.submission_manage, name='submission_manage'),
]
