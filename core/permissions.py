from accounts.models import User


def is_platform_admin(user):
    """Administradores da plataforma: superusuário ou papel "Administrador"."""
    return user.is_authenticated and (user.is_superuser or user.role == User.Role.ADMIN)
