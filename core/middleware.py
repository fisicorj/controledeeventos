from .models import request_cache


class RequestCacheMiddleware:
    """Abre um cache vazio no começo de cada requisição e o descarta no fim (ver core.models.request_cache)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = request_cache.set({})
        try:
            return self.get_response(request)
        finally:
            request_cache.reset(token)


# ---------- Cabeçalhos de segurança (SEC-07)

CSP_DIRECTIVES = {
    'default-src': ["'self'"],
    # Nenhum script inline: os comportamentos das páginas ficam em static/js/ (ver app.js).
    # O Bootstrap também vem do próprio portal (static/vendor/bootstrap/): nenhum domínio de terceiros.
    'script-src': ["'self'"],
    # Atributos style="" e a cor da instituição em <style> continuam liberados (risco baixo; não executam código).
    'style-src': ["'self'", "'unsafe-inline'"],
    'img-src': ["'self'", 'data:', 'blob:'],
    'font-src': ["'self'", 'data:'],
    'connect-src': ["'self'"],
    'media-src': ["'self'", 'blob:'],
    'worker-src': ["'self'"],
    'manifest-src': ["'self'"],
    'object-src': ["'none'"],
    'base-uri': ["'self'"],
    'form-action': ["'self'"],
    'frame-ancestors': ["'none'"],
}

# Câmera só nas telas de leitura de QR (as views marcam com allow_camera); o resto do portal não usa.
PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=(), payment=(), usb=()'
PERMISSIONS_POLICY_CAMERA = 'camera=(self), microphone=(), geolocation=(), payment=(), usb=()'


def build_csp(report_uri=None):
    directives = dict(CSP_DIRECTIVES)
    if report_uri:
        directives['report-uri'] = [report_uri]
    return '; '.join(f'{name} {" ".join(values)}' for name, values in directives.items())


def allow_camera(view):
    """Decorator: a página usa a câmera (leitor de QR)."""
    from functools import wraps

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response['Permissions-Policy'] = PERMISSIONS_POLICY_CAMERA
        return response

    return wrapper


class SecurityHeadersMiddleware:
    """Content-Security-Policy e Permissions-Policy em todas as respostas.

    CSP_MODE (settings/.env): "enforce" (padrão) bloqueia o que estiver fora da política; "report" só avisa
    (Content-Security-Policy-Report-Only); "off" desliga. Nos dois primeiros modos o navegador manda as violações
    para /csp-report/, que registra no log (ver core.views.csp_report).
    """

    def __init__(self, get_response):
        from django.conf import settings
        from django.urls import reverse

        self.get_response = get_response
        self.mode = getattr(settings, 'CSP_MODE', 'enforce')
        self.header = 'Content-Security-Policy-Report-Only' if self.mode == 'report' else 'Content-Security-Policy'
        self.policy = build_csp(reverse('csp_report')) if self.mode in ('enforce', 'report') else None

    def __call__(self, request):
        response = self.get_response(request)
        if self.policy and self.header not in response:
            response[self.header] = self.policy
        response.setdefault('Permissions-Policy', PERMISSIONS_POLICY)
        return response


# ---------- Verificação de saúde (/saude/)

HEALTH_PATH = '/saude/'


class HealthCheckMiddleware:
    """Responde /saude/ antes de qualquer outro middleware: sem checagem de host (ALLOWED_HOSTS) nem
    redirecionamento para HTTPS, para o healthcheck do Docker e o monitor externo funcionarem por
    http://127.0.0.1:8000/saude/. Testa o banco; 200 quando está tudo certo, 503 quando não.

    Não mostra dados internos além do estado de cada parte. Fica no topo da lista MIDDLEWARE."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path != HEALTH_PATH or request.method not in ('GET', 'HEAD'):
            return self.get_response(request)
        from django.db import connection
        from django.http import JsonResponse

        checks = {}
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            checks['banco'] = 'ok'
        except Exception:
            checks['banco'] = 'erro'
        healthy = all(value == 'ok' for value in checks.values())
        response = JsonResponse({'situacao': 'ok' if healthy else 'erro', **checks}, status=200 if healthy else 503)
        response['Cache-Control'] = 'no-store'
        return response
