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
    }
