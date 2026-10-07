from .models import SiteSettings


def portal(request):
    """Disponibiliza as configurações da instituição (logo, nome do portal, cor) em todos os templates.

    O nome é "portal" (e não "site") porque as telas de login e senha do Django já usam "site".
    """
    return {'portal': SiteSettings.load()}
