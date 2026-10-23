from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Plataforma'

    def ready(self):
        from . import checks  # noqa: F401 (registra as verificações do "manage.py check --deploy")
        from .images import restrict_pillow

        restrict_pillow()  # só JPEG, PNG e WebP (core/images.py)
