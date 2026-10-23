"""Avisos de configuração mostrados pelo "python manage.py check --deploy" (SEC-13)."""

import os

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
    if provisional_site_url():
        warnings.append(
            Warning(
                f'SITE_URL ({settings.SITE_URL}) é um endereço provisório da AWS.',
                hint='O QR Code e o link de validação dos certificados guardam este endereço. Defina o domínio '
                'definitivo antes de emitir certificados reais: os emitidos com o endereço provisório deixam de abrir '
                'quando ele for trocado.',
                id='arena.W005',
            )
        )
    # Senha do papel da aplicação e, se houver, a do dono das tabelas (só presente onde roda o migrate).
    passwords = [('DB_PASSWORD', settings.DATABASES['default'].get('PASSWORD') or '')]
    if settings.DB_MIGRATE_USER:
        passwords.append(('DB_MIGRATE_PASSWORD', os.getenv('DB_MIGRATE_PASSWORD', '')))
    for name, password in passwords:
        if _weak(password):
            warnings.append(
                Warning(
                    f'A senha do banco ({name}) é fraca ou é a do exemplo.',
                    hint='Use uma senha longa e aleatória, ex.: '
                    'python -c "import secrets; print(secrets.token_urlsafe(24))".',
                    id='arena.W004',
                )
            )
    return warnings


PROVISIONAL_HOSTS = ('.cloudfront.net', '.elb.amazonaws.com')


def provisional_site_url():
    """O endereço público é um nome gerado pela AWS (CloudFront ou ALB), que muda se a infraestrutura mudar?"""
    from urllib.parse import urlsplit

    host = (urlsplit(settings.SITE_URL).hostname or '').lower()
    return host.endswith(PROVISIONAL_HOSTS)


WEAK_PASSWORDS = {'', 'arena_password', 'postgres', 'password', 'senha', '123456'}


def _weak(password):
    return password.lower() in WEAK_PASSWORDS or len(password) < 12
