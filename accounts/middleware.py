from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

from . import mfa


class MfaMiddleware:
    """Administradores e organizadores só usam o sistema depois do segundo fator.

    - Com a verificação em duas etapas ativa: depois da senha, pede o código do aplicativo (a cada login).
    - Sem ela e com MFA_REQUIRED ligado: leva para o cadastro antes de qualquer outra página.

    Fica no middleware (e não só na tela de login) para valer em todos os caminhos de entrada:
    login do portal, login do /admin/, troca de senha etc.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and mfa.applies_to(user) and not mfa.is_verified(request):
            target = self.redirect_for(request, user)
            if target:
                return target
        return self.get_response(request)

    def redirect_for(self, request, user):
        path = request.path
        if path.startswith((settings.STATIC_URL, settings.MEDIA_URL, reverse('logout'))):  # logo da tela de código
            return None
        if mfa.device_for(user) is not None:
            verify_url = reverse('mfa_verify')
            if path == verify_url:
                return None
            return redirect(f'{verify_url}?{urlencode({"next": request.get_full_path()})}')
        if settings.MFA_REQUIRED:
            setup_url = reverse('mfa_setup')
            if path in (setup_url, reverse('mfa_codes')):
                return None
            return redirect(setup_url)
        return None
