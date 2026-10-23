import time

from django.utils.functional import SimpleLazyObject

from .models import SiteSettings
from .permissions import is_organizer, is_platform_admin


def portal(request):
    """Disponibiliza as configurações da instituição (logo, nome do portal, cor) em todos os templates.

    O nome é "portal" (e não "site") porque as telas de login e senha do Django já usam "site".
    Também diz aos templates o que o usuário pode ver no menu (as regras ficam em core/permissions.py).
    """
    user = getattr(request, 'user', None)
    return {
        'portal': SiteSettings.load(),
        'user_is_admin': SimpleLazyObject(lambda: user is not None and is_platform_admin(user)),
        'user_is_organizer': SimpleLazyObject(lambda: user is not None and is_organizer(user)),
        'user_reviews': SimpleLazyObject(lambda: _menu(request, user)['reviews']),
        'provisional_site_url': SimpleLazyObject(_provisional),
        'user_can_checkin': SimpleLazyObject(lambda: _menu(request, user)['checkin']),
    }


# O-05: os itens "Avaliações" e "Check-in" do menu custam algumas consultas. O resultado fica na sessão por
# MENU_SECONDS segundos; as telas que mudam esses números (entregar ou recusar avaliação, aceitar convite) chamam
# forget_menu para a mudança aparecer na hora.
MENU_KEY = 'menu_cache'
MENU_SECONDS = 300


def _menu(request, user):
    if user is None or not user.is_authenticated or not hasattr(request, 'session'):
        return {'reviews': _review_menu(user), 'checkin': _can_checkin(user)}
    cached = request.session.get(MENU_KEY)
    now = int(time.time())
    if not cached or cached.get('user') != user.pk or now - cached.get('at', 0) >= MENU_SECONDS:
        cached = {'user': user.pk, 'at': now, 'reviews': _review_menu(user), 'checkin': _can_checkin(user)}
        request.session[MENU_KEY] = cached
    return cached


def forget_menu(request):
    if hasattr(request, 'session'):
        request.session.pop(MENU_KEY, None)


def _review_menu(user):
    from submissions.services import review_menu

    return review_menu(user) if user is not None else {'show': False, 'pending': 0}


def _provisional():
    from .checks import provisional_site_url

    return provisional_site_url()


def _can_checkin(user):
    """Atalho de check-in no menu e no botão do celular: organização, administração e equipe de check-in."""
    from .permissions import can_manage

    if user is None or not user.is_authenticated:
        return False
    return can_manage(user) or user.checkin_activities.exists()
