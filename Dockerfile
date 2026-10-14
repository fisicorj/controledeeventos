# Para fixar a imagem exata (recomendado em produção), troque pela versão com digest, que o comando abaixo mostra:
#   docker pull python:3.12-slim && docker inspect --format '{{index .RepoDigests 0}}' python:3.12-slim
# Ex.: FROM python:3.12-slim@sha256:<digest>. O Dependabot avisa quando sair versão nova.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# A chave abaixo existe só para o collectstatic rodar durante o build; a real vem do .env.
RUN SECRET_KEY=collectstatic-build-only-not-used-at-runtime-0123456789 DEBUG=False python manage.py collectstatic --noinput

RUN useradd --create-home app && mkdir -p /app/media /app/private_media && chown -R app:app /app/media /app/private_media
USER app

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py setup_db_roles --if-configured && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --timeout ${GUNICORN_TIMEOUT:-120} --access-logfile -"]
