"""Confirmação de identidade antes de ações críticas (SEC-19 da 2ª auditoria).

Revogar certificado, cancelar emissão avulsa e mexer na configuração de e-mail pedem que a pessoa tenha provado
quem é há pouco tempo: o login (com a segunda etapa) vale por REAUTH_MINUTES; depois disso, a tela pede a senha
de novo (e o código do aplicativo, para quem usa a verificação em duas etapas). Quem pegou uma sessão aberta
esquecida não consegue fazer essas ações.
"""

from functools import wraps

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

SESSION_KEY = 'reauth_at'


def mark(request):
    request.session[SESSION_KEY] = timezone.now().timestamp()


def is_recent(request):
    stamp = request.session.get(SESSION_KEY)
    return bool(stamp) and timezone.now().timestamp() - stamp < settings.REAUTH_MINUTES * 60


def require_recent_auth(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and not is_recent(request):
            target = request.get_full_path() if request.method == 'GET' else request.META.get('HTTP_REFERER', '')
            return redirect(f'{reverse("reauth")}?{urlencode({"next": target or "/"})}')
        return view(request, *args, **kwargs)

    return wrapper
