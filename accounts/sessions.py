"""Sessões abertas de cada conta: registro e encerramento (ex.: depois de apagar a verificação em duas etapas,
desativar a conta pelo painel ou no reset_mfa).

Cada sessão autenticada é registrada em UserSession na primeira requisição (SessionTrackingMiddleware), inclusive
as que já existiam antes deste registro. Assim, encerrar as sessões de uma pessoa é uma consulta direta."""

from django.contrib.sessions.models import Session

TRACKED_KEY = '_tracked_session'


def track(request):
    """Registra a sessão atual da pessoa logada, se ainda não estiver registrada com a chave atual
    (a chave muda no login e na troca de senha)."""
    from .models import UserSession

    session = getattr(request, 'session', None)
    user = getattr(request, 'user', None)
    if session is None or user is None or not user.is_authenticated:
        return
    key = session.session_key
    if not key or session.get(TRACKED_KEY) == key:
        return
    UserSession.objects.update_or_create(session_key=key, defaults={'user': user})
    session[TRACKED_KEY] = key


def end_sessions(user):
    """Apaga as sessões da pessoa em qualquer aparelho. Devolve quantas.

    As registradas saem por consulta direta. As que ainda não passaram pelo registro (abertas antes dele e sem
    uso desde então) são conferidas uma a uma; com o tempo quase todas estão registradas e essa parte fica curta."""
    from django.utils import timezone

    from .models import UserSession

    keys = list(UserSession.objects.filter(user=user).values_list('session_key', flat=True))
    ended = Session.objects.filter(session_key__in=keys).delete()[0]
    UserSession.objects.filter(user=user).delete()
    untracked = Session.objects.filter(expire_date__gt=timezone.now()).exclude(
        session_key__in=UserSession.objects.values('session_key')
    )
    for session in untracked.iterator():
        if str(session.get_decoded().get('_auth_user_id')) == str(user.pk):
            session.delete()
            ended += 1
    return ended


def forget_closed():
    """Tira do registro as sessões que já não existem (saíram, venceram). Roda na faxina diária."""
    from .models import UserSession

    return UserSession.objects.exclude(session_key__in=Session.objects.values('session_key')).delete()[0]


class SessionTrackingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        track(request)  # depois da view: no login, a chave da sessão já é a nova
        return response
