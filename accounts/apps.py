from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        user_logged_in.connect(shorter_privileged_session, dispatch_uid='accounts.shorter_privileged_session')
        user_logged_in.connect(mark_recent_auth, dispatch_uid='accounts.mark_recent_auth')


def mark_recent_auth(sender, request, user, **kwargs):
    """O login com senha conta como identidade confirmada agora (ver accounts/reauth.py)."""
    from . import reauth

    if request is not None and hasattr(request, 'session'):
        reauth.mark(request)


def shorter_privileged_session(sender, request, user, **kwargs):
    """Administrador e organizador: a sessão termina PRIVILEGED_SESSION_HOURS depois do login (padrão 8),
    em vez das 2 semanas de quem é participante. Uma senha vazada vale por menos tempo."""
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from . import mfa

    if request is not None and hasattr(request, 'session') and mfa.applies_to(user):
        request.session.set_expiry(timezone.now() + timedelta(hours=settings.PRIVILEGED_SESSION_HOURS))
