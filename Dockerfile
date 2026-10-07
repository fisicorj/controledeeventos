FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# A chave abaixo existe só para o collectstatic rodar durante o build; a real vem do .env.
RUN SECRET_KEY=collectstatic-build-only DEBUG=False python manage.py collectstatic --noinput

RUN useradd --create-home app && mkdir -p /app/media && chown -R app:app /app/media
USER app

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --access-logfile -"]
