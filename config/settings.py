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
if SECRET_KEY in _PLACEHOLDER_KEYS:
    if DEBUG:
        SECRET_KEY = 'development-only-key'
    else:
        raise ImproperlyConfigured(
            'Defina SECRET_KEY no .env (obrigatória com DEBUG=False). Gere uma com: '
            'python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"'
        )

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', '127.0.0.1,localhost')
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
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
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
    }
}
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

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'accounts.User'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
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

# Bootstrap usa 'danger' em vez de 'error'
MESSAGE_TAGS = {message_constants.ERROR: 'danger'}

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
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'}},
    'root': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO')},
    'loggers': {'django': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO'), 'propagate': False}},
}
