from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

from activities.views import home

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home, name='home'),
    path('accounts/', include('accounts.urls')),
    path('activities/', include('activities.urls')),
    path('registrations/', include('registrations.urls')),
    path('attendance/', include('attendance.urls')),
    path('certificados/', include('certificates.urls')),
    path('configuracoes/', include('core.urls')),
]

if settings.DEBUG or settings.SERVE_MEDIA:
    urlpatterns += [re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]
