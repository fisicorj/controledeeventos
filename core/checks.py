"""Avisos de configuração mostrados pelo "python manage.py check --deploy" (SEC-13)."""

from django.conf import settings
from django.core.checks import Tags, Warning, register


@register(Tags.security, deploy=True)
def arena_deploy_checks(app_configs, **kwargs):
    warnings = []
    if settings.SERVE_MEDIA and not settings.DEBUG:
        warnings.append(
            Warning(
                'SERVE_MEDIA=True: o Django está servindo /media/, o que não é recomendado em produção.',
                hint='Configure o proxy (nginx, Caddy) para servir a pasta media/ e deixe SERVE_MEDIA=False.',
                id='arena.W001',
            )
        )
    host = settings.DATABASES['default'].get('HOST') or ''
    local = host in ('', 'localhost', '127.0.0.1', '::1', 'db')
    if not local and not settings.DATABASES['default'].get('OPTIONS', {}).get('sslmode'):
        warnings.append(
            Warning(
                f'O banco fica em outro servidor ({host}) e a conexão não pede criptografia.',
                hint='Defina DB_SSLMODE=require (ou verify-full) no .env.',
                id='arena.W002',
            )
        )
    if settings.ADMIN_URL == 'admin/':
        warnings.append(
            Warning(
                'O painel administrativo está no endereço padrão /admin/.',
                hint='Defina ADMIN_URL no .env (ex.: ADMIN_URL=gestao-arena/) e, se possível, restrinja por IP '
                'no proxy.',
                id='arena.W003',
            )
        )
    return warnings
