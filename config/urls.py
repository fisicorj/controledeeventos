from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

from accounts.forms import ThrottledAdminAuthenticationForm
from accounts.views import admin_logout
from activities.views import calendar_ics, event_page, home
from core.views import csp_report, privacy

# O login do /admin/ tem o mesmo limite de tentativas do login do portal.
admin.site.login_form = ThrottledAdminAuthenticationForm

urlpatterns = [
    path(f'{settings.ADMIN_URL}logout/', admin_logout),  # antes do admin, para valer no lugar do logout padrão
    path(settings.ADMIN_URL, admin.site.urls),
    path('', home, name='home'),
    path('privacidade/', privacy, name='privacy'),
    path('csp-report/', csp_report, name='csp_report'),
    path('eventos/<slug:slug>/', event_page, name='event_page'),
    path('eventos/<slug:slug>/agenda.ics', calendar_ics, name='event_calendar'),
    path('accounts/', include('accounts.urls')),
    path('activities/', include('activities.urls')),
    path('registrations/', include('registrations.urls')),
    path('attendance/', include('attendance.urls')),
    path('certificados/', include('certificates.urls')),
    path('configuracoes/', include('core.urls')),
]

if settings.DEBUG or settings.SERVE_MEDIA:
    urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]
