"""Fábrica de dados compartilhada pelos testes dos apps."""

from datetime import timedelta

from django.utils import timezone

from accounts.models import User
from activities.models import Activity, Session

PASSWORD = 'Senha!forte123'
DT_FORMAT = '%Y-%m-%dT%H:%M'


def local(dt):
    """Formata como o campo datetime-local do formulário envia."""
    return timezone.localtime(dt).strftime(DT_FORMAT)


def now():
    return timezone.now().replace(second=0, microsecond=0)


def make_user(username, role=User.Role.PARTICIPANT, **extra):
    return User.objects.create_user(username, f'{username}@exemplo.com', PASSWORD, role=role, **extra)


def make_activity(organizer, **overrides):
    n = now()
    data = {
        'title': 'Curso de Django',
        'description': 'Descrição',
        'activity_type': Activity.Type.COURSE,
        'start_date': n + timedelta(days=1),
        'end_date': n + timedelta(days=3),
        'registration_start': n - timedelta(days=1),
        'registration_end': n + timedelta(hours=12),
        'capacity': 2,
        'organizer': organizer,
        'status': Activity.Status.PUBLISHED,
    }
    data.update(overrides)
    return Activity.objects.create(**data)


def make_session(activity, **overrides):
    data = {
        'activity': activity,
        'title': 'Aula 1',
        'start_at': activity.start_date,
        'end_at': activity.start_date + timedelta(hours=2),
        'check_in_open': True,
    }
    data.update(overrides)
    return Session.objects.create(**data)
