"""Limite de tentativas (login, recuperação de senha, códigos), guardado no banco.

Funciona igual com vários processos do gunicorn, porque o contador fica no PostgreSQL. As chaves são gravadas
como hash: o banco não guarda e-mails nem IPs em texto."""

import hashlib
import hmac
import random
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Throttle


def _key(*parts):
    """HMAC com a SECRET_KEY: sem a chave, não dá para descobrir o IP ou o e-mail testando todos os valores
    possíveis (um hash simples de um IPv4 se quebra em minutos)."""
    raw = ':'.join(str(p).strip().lower() for p in parts)
    secret = f'arena:throttle:{settings.SECRET_KEY}'.encode()
    return hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()


def _current(row, period, now):
    return row is not None and now - row.window_start < timedelta(seconds=period)


def blocked(limit, period, *parts):
    """Já passou do limite nesta janela? (não conta uma nova tentativa)"""
    now = timezone.now()
    row = Throttle.objects.filter(key=_key(*parts)).first()
    return _current(row, period, now) and row.count >= limit


def hit(limit, period, *parts):
    """Registra uma tentativa. Devolve True se ainda está dentro do limite."""
    key, now = _key(*parts), timezone.now()
    with transaction.atomic():
        row = Throttle.objects.select_for_update().filter(key=key).first()
        if row is None:
            try:
                with transaction.atomic():
                    row = Throttle.objects.create(key=key, window_start=now, count=0)
            except IntegrityError:  # outra requisição criou ao mesmo tempo
                row = Throttle.objects.select_for_update().get(key=key)
        if not _current(row, period, now):
            row.window_start, row.count = now, 0
        row.count += 1
        row.save(update_fields=['window_start', 'count'])
    if random.random() < 0.01:  # noqa: S311 faxina ocasional (sorteio sem uso criptográfico)
        Throttle.objects.filter(window_start__lt=now - timedelta(days=2)).delete()
    return row.count <= limit


def reset(*parts):
    Throttle.objects.filter(key=_key(*parts)).delete()
