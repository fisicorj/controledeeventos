from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    class Role(models.TextChoices):
        PARTICIPANT = 'PARTICIPANT', 'Participante'
        ORGANIZER = 'ORGANIZER', 'Organizador'
        ADMIN = 'ADMIN', 'Administrador'

    email = models.EmailField(_('email address'), unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PARTICIPANT)
    institutional_id = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return self.get_full_name() or self.username
