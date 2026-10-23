"""Navegador conhecido: depois de um login com a senha certa, o navegador guarda um cookie assinado que diz "este
usuário já entrou daqui".

Serve para o teto de senhas erradas por usuário (50 em 15 minutos, somando todos os IPs). Sem isso, quem sabe o
nome de usuário de outra pessoa consegue travar o login dela de propósito, errando a senha de vários lugares.
Com o cookie, o dono continua entrando do navegador que já usou; quem nunca entrou com a senha certa daquele
navegador fica bloqueado normalmente. O limite de 5 erros por usuário e IP vale sempre.

O cookie guarda só um hash do usuário (num computador compartilhado, não mostra quem entrou), vale 180 dias e,
por ser assinado com a SECRET_KEY, não pode ser inventado.
"""

import hashlib
import hmac

from django.conf import settings
from django.core import signing

COOKIE = 'arena_known'
SALT = 'arena.known-device'
MAX_AGE = 180 * 24 * 3600
MAX_USERS = 5  # usuários lembrados por navegador (o mais antigo sai)


def _tag(username):
    secret = f'arena:known:{settings.SECRET_KEY}'.encode()
    return hmac.new(secret, username.strip().lower().encode(), hashlib.sha256).hexdigest()[:24]


def _tags(request):
    raw = request.COOKIES.get(COOKIE) if request is not None else None
    if not raw:
        return []
    try:
        value = signing.loads(raw, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return []
    return [t for t in value if isinstance(t, str)] if isinstance(value, list) else []


def is_known(request, username):
    return bool(username) and _tag(username) in _tags(request)


def remember(request, username):
    """Marca o navegador como conhecido para este usuário (o cookie sai na resposta, pelo middleware)."""
    if request is None or not username:
        return
    tag = _tag(username)
    tags = [t for t in _tags(request) if t != tag] + [tag]
    request._known_device_tags = tags[-MAX_USERS:]


class KnownDeviceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        tags = getattr(request, '_known_device_tags', None)
        if tags:
            response.set_cookie(
                COOKIE,
                signing.dumps(tags, salt=SALT),
                max_age=MAX_AGE,
                secure=settings.SESSION_COOKIE_SECURE,
                httponly=True,
                samesite='Lax',
            )
        return response
