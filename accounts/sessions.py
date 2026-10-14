"""Encerrar as sessões abertas de uma conta (ex.: depois de apagar a verificação em duas etapas)."""

from django.contrib.sessions.models import Session
from django.utils import timezone


def end_sessions(user):
    """Apaga as sessões ativas da pessoa em qualquer aparelho. Devolve quantas."""
    ended = 0
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if str(session.get_decoded().get('_auth_user_id')) == str(user.pk):
            session.delete()
            ended += 1
    return ended
