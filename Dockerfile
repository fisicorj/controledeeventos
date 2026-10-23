# Para fixar a imagem exata (recomendado em produção), rode uma vez: bash scripts/pin_base_image.sh
# O script troca a linha abaixo por python:3.12-slim@sha256:<digest>. O Dependabot avisa quando sair versão nova.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
# requirements.lock: as mesmas versões do requirements.txt, mais as dependências delas, cada uma com o hash dos
# arquivos publicados. Um pacote trocado no caminho (ou no próprio índice) é recusado (SEC-20).
COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock

COPY . .
# A chave abaixo existe só para o collectstatic rodar durante o build; a real vem do .env.
RUN SECRET_KEY=collectstatic-build-only-not-used-at-runtime-0123456789 DEBUG=False python manage.py collectstatic --noinput

# UID fixo: os volumes EFS da AWS (infra/setup.sh) entregam as pastas para o UID 1000.
RUN useradd --create-home --uid 1000 app && mkdir -p /app/media /app/private_media && chown -R app:app /app/media /app/private_media
USER app

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py setup_db_roles --if-configured && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --timeout ${GUNICORN_TIMEOUT:-120} --access-logfile -"]
