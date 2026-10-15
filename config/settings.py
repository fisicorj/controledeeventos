import os
import sys
from pathlib import Path

from django.contrib.messages import constants as message_constants
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    value = os.getenv(name)
    return default if value is None or value.strip() == '' else value.strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


DEBUG = env_bool('DEBUG', False)
TESTING = len(sys.argv) > 1 and sys.argv[1] == 'test'

# Em produção (DEBUG=False) a chave é obrigatória: sem ela, sessões e tokens CSRF ficam previsíveis.
SECRET_KEY = os.getenv('SECRET_KEY', '').strip()
_PLACEHOLDER_KEYS = {'', 'replace-with-a-long-random-secret', 'development-only-key'}
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', '127.0.0.1,localhost')
_LOCAL_HOSTS = {'127.0.0.1', 'localhost', '[::1]', '::1'}
if SECRET_KEY in _PLACEHOLDER_KEYS or len(SECRET_KEY) < 50:  # o mesmo mínimo que o check --deploy do Django
    # A chave de exemplo só é aceita em desenvolvimento na própria máquina. Em qualquer servidor
    # acessível pela rede ela deixaria sessões, links de senha e a senha do SMTP expostos.
    if TESTING or (DEBUG and set(ALLOWED_HOSTS) <= _LOCAL_HOSTS and SECRET_KEY in _PLACEHOLDER_KEYS):
        SECRET_KEY = 'development-only-key'  # noqa: S105 só em testes e desenvolvimento local
    else:
        raise ImproperlyConfigured(
            'Defina uma SECRET_KEY própria no .env (pelo menos 50 caracteres). Gere uma com: '
            'python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"'
        )

# Necessário quando o site é acessado por HTTPS ou por outro endereço (ex.: https://eventos.exemplo.br)
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'accounts',
    'activities',
    'registrations',
    'attendance',
    'certificates',
    'core',
]
MIDDLEWARE = [
    'core.middleware.HealthCheckMiddleware',  # /saude/: antes da checagem de host e do redirecionamento HTTPS
    'django.middleware.security.SecurityMiddleware',
    'core.middleware.SecurityHeadersMiddleware',
    'core.middleware.RequestCacheMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'accounts.middleware.MfaMiddleware',
    'accounts.sessions.SessionTrackingMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.portal',
            ]
        },
    }
]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '60')),
        'CONN_HEALTH_CHECKS': True,
        # Banco em outro servidor: DB_SSLMODE=require (ou verify-full) criptografa a conexão.
        'OPTIONS': {'sslmode': os.getenv('DB_SSLMODE')} if os.getenv('DB_SSLMODE') else {},
    }
}
# Dois papéis no banco (SEC-03): DB_USER é o da aplicação, sem permissão de mudar a estrutura nem de alterar ou
# apagar a auditoria; DB_MIGRATE_USER é o dono das tabelas, usado só pelos comandos que mudam a estrutura.
# Sem DB_MIGRATE_USER, um único usuário faz tudo (desenvolvimento). Ver README, "Banco: dois papéis".
DB_MIGRATE_USER = os.getenv('DB_MIGRATE_USER', '').strip()
SCHEMA_COMMANDS = {'migrate', 'setup_db_roles', 'validate_constraints'}
if DB_MIGRATE_USER and len(sys.argv) > 1 and sys.argv[1] in SCHEMA_COMMANDS:
    DATABASES['default']['USER'] = DB_MIGRATE_USER
    DATABASES['default']['PASSWORD'] = os.getenv('DB_MIGRATE_PASSWORD', '')
# Endereço do painel administrativo do Django. Trocar o padrão "admin/" reduz tentativas automáticas.
ADMIN_URL = (os.getenv('ADMIN_URL') or 'admin/').strip('/') + '/'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# Arquivos estáticos: em produção, "python manage.py collectstatic" copia tudo para STATIC_ROOT
# e o WhiteNoise serve com compressão e cache longo.
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    # Nos testes e em DEBUG não há collectstatic, então o armazenamento simples é usado.
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'
        if DEBUG or TESTING
        else 'whitenoise.storage.CompressedManifestStaticFilesStorage'
    },
}

# Uploads (imagens das atividades). Em produção o ideal é o proxy (nginx/Caddy) servir /media/;
# SERVE_MEDIA=True faz o próprio Django servir, adequado para instalações pequenas.
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'
SERVE_MEDIA = env_bool('SERVE_MEDIA', False)
# Arquivos privados (materiais pós-evento): nunca são servidos direto, só pela view que confere quem pode baixar.
PRIVATE_MEDIA_ROOT = Path(os.getenv('PRIVATE_MEDIA_ROOT', BASE_DIR / 'private_media'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'accounts.User'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
# Verificação em duas etapas (accounts/mfa.py) para administradores e organizadores.
# MFA_REQUIRED=True obriga o cadastro no primeiro acesso; com False, só quem ativou em "Meus dados" usa.
# Padrão: obrigatória em produção, opcional com DEBUG=True (desenvolvimento) e nos testes.
MFA_REQUIRED = env_bool('MFA_REQUIRED', not DEBUG and not TESTING)
# Duração máxima da sessão de administradores e organizadores, em horas (participantes: 2 semanas).
PRIVILEGED_SESSION_HOURS = int(os.getenv('PRIVILEGED_SESSION_HOURS') or '8')
# Ações críticas (revogar certificado, cancelar emissão, configuração de e-mail) pedem senha de novo depois de
# tantos minutos do login ou da última confirmação (accounts/reauth.py).
REAUTH_MINUTES = int(os.getenv('REAUTH_MINUTES') or '15')
LOGOUT_REDIRECT_URL = 'home'

# ---------- Endereço público
# Usado nos links dos e-mails enviados fora de uma requisição (ex.: lembretes agendados) e no QR do certificado.
# Nome da instituição, cidade, logo e assinatura dos certificados são configurados dentro da plataforma
# (menu "Configurações", visível para administradores).
SITE_URL = os.getenv('SITE_URL', 'http://127.0.0.1:8000').rstrip('/')

# ---------- E-mail (recuperação de senha, confirmações, lembretes e certificados)
# O servidor SMTP/relay, o remetente e a senha são configurados dentro da plataforma
# (Configurações > E-mail). Enquanto o envio estiver desligado lá, os e-mails só aparecem no terminal/log.
EMAIL_BACKEND = 'core.mail.PlatformEmailBackend'
DEFAULT_FROM_EMAIL = 'nao-responda@localhost'  # usado só se nenhum remetente for configurado na plataforma
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24  # link válido por 24 horas
# Os e-mails saem em texto e também numa versão HTML com a cor e o nome da instituição (core/mail_html.py).
EMAIL_HTML = env_bool('EMAIL_HTML', True)
# Fila de e-mails (core/outbox.py): "thread" envia em segundo plano no próprio servidor, logo depois de cada
# operação; "off" deixa só para o comando "process_outbox" (serviço separado). Nos testes, envia na hora.
OUTBOX_MODE = os.getenv('OUTBOX_MODE', 'sync' if TESTING else 'thread').strip().lower()
# Dias que destinatário e conteúdo de um e-mail enviado ficam guardados (0 = para sempre). Depois disso a
# limpeza (serviço worker ou comando "purge_outbox") mantém só a contagem dos envios.
OUTBOX_RETENTION_DAYS = int(os.getenv('OUTBOX_RETENTION_DAYS') or '30')
# "Tentar de novo" em Configurações > E-mail só reenvia as falhas destes últimos dias (avisos antigos confundiriam).
OUTBOX_RETRY_DAYS = int(os.getenv('OUTBOX_RETRY_DAYS') or '3')
# Pasta para onde o worker exporta a auditoria uma vez por dia (core/audit_export.py). Em branco: não exporta.
AUDIT_EXPORT_DIR = os.getenv('AUDIT_EXPORT_DIR', '').strip()
# Quem recebe, a cada exportação, o último hash da cadeia (âncora fora do servidor). Em branco: o e-mail de alertas
# de erro de Configurações > E-mail.
AUDIT_EXPORT_EMAIL = os.getenv('AUDIT_EXPORT_EMAIL', '').strip()

# Bootstrap usa 'danger' em vez de 'error'
MESSAGE_TAGS = {message_constants.ERROR: 'danger'}

# ---------- Content-Security-Policy (core/middleware.py)
# enforce: bloqueia scripts e recursos fora da política (padrão); report: só registra no log; off: desliga.
CSP_MODE = (os.getenv('CSP_MODE') or 'enforce').strip().lower()

# ---------- Proxy reverso e HTTPS
# NUM_PROXIES: quantos proxies confiáveis existem na frente da aplicação (0 = acesso direto).
# Só com NUM_PROXIES > 0 o cabeçalho X-Forwarded-For é usado para obter o IP do cliente.
NUM_PROXIES = int(os.getenv('NUM_PROXIES', '0'))
if NUM_PROXIES > 0:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# HTTPS_ENABLED vale True por padrão em produção. Desligue apenas para testes em rede local sem certificado.
HTTPS_ENABLED = env_bool('HTTPS_ENABLED', not DEBUG)
SESSION_COOKIE_SECURE = HTTPS_ENABLED
CSRF_COOKIE_SECURE = HTTPS_ENABLED
# O cliente de testes do Django usa HTTP, então o redirecionamento fica desligado ao rodar os testes.
SECURE_SSL_REDIRECT = HTTPS_ENABLED and not TESTING and env_bool('SECURE_SSL_REDIRECT', True)
# HSTS faz o navegador recusar HTTP por um período. Comece com um valor baixo (ex.: 3600)
# e aumente (31536000) só depois de confirmar que o HTTPS está estável.
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0')) if HTTPS_ENABLED else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SESSION_COOKIE_HTTPONLY = True

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'simple': {'format': '{asctime} {levelname} {name}: {message}', 'style': '{'}},
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'},
        # Erro 500: avisa por e-mail quem estiver em Configurações > E-mail > "E-mail para alertas de erro".
        'error_alert': {'class': 'core.alerts.ErrorAlertHandler', 'level': 'ERROR'},
    },
    'root': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO')},
    'loggers': {
        'django': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO'), 'propagate': False},
        'django.request': {'handlers': ['console', 'error_alert'], 'level': 'WARNING', 'propagate': False},
    },
}
