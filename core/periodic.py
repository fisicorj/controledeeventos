"""Tarefas periódicas do worker (lembretes, prazos da fila, faxina) sem repetir entre reinícios e entre workers.

Antes, cada worker guardava na memória quando tinha rodado cada tarefa: a cada deploy (e com dois workers juntos na
troca de versão) tudo rodava de novo na hora, e o mesmo lembrete podia sair duas vezes. Agora a hora fica no banco
(PeriodicTask) e a vez é disputada com SELECT ... FOR UPDATE: só um pega.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import PeriodicTask


def claim(name, every_seconds, now=None):
    """True se esta chamada pegou a vez da tarefa (ninguém a rodou nos últimos every_seconds); grava a hora."""
    now = now or timezone.now()
    try:
        with transaction.atomic():
            row, _ = PeriodicTask.objects.select_for_update().get_or_create(name=name)
            if row.last_run_at and now - row.last_run_at < timedelta(seconds=every_seconds):
                return False
            row.last_run_at = now
            row.save(update_fields=['last_run_at'])
            return True
    except IntegrityError:  # outro worker criou a linha no mesmo instante: a vez é dele
        return False
