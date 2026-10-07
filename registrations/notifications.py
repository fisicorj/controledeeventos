"""E-mails enviados aos participantes.

Os envios acontecem depois que a transação do banco é gravada (transaction.on_commit), para nunca
avisar algo que acabou não sendo salvo. Uma falha no servidor de e-mail é registrada no log e não
interrompe a operação do usuário.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

from core.models import SiteSettings

from .qr import qr_png

logger = logging.getLogger(__name__)


def absolute_url(path):
    return settings.SITE_URL + path


def build_message(to, subject, template, context, attachments=()):
    institution = SiteSettings.load().institution_name
    context = {
        'institution': institution,
        'my_registrations_url': absolute_url(reverse('my_registrations')),
        **context,
    }
    body = render_to_string(f'emails/{template}.txt', context)
    message = EmailMessage(f'{institution}: {subject}', body, to=[to])
    for filename, content, mimetype in attachments:
        message.attach(filename, content, mimetype)
    return message


def send_messages(messages):
    messages = [m for m in messages if m.to and m.to[0]]
    if not messages:
        return 0
    try:
        with get_connection() as connection:
            return connection.send_messages(messages) or 0
    except Exception:
        logger.exception('Falha ao enviar %d e-mail(s).', len(messages))
        return 0


def send_on_commit(build):
    """Monta e envia as mensagens só depois que a transação atual for gravada."""
    transaction.on_commit(lambda: send_messages(build()))


def _registration_context(registration):
    activity = registration.activity
    return {
        'user': registration.user,
        'name': registration.user.first_name or registration.user.username,
        'activity': activity,
        'activity_url': absolute_url(reverse('activity_detail', args=[activity.id])),
    }


def _qr_attachment(registration):
    return [('qrcode-inscricao.png', qr_png(registration), 'image/png')]


# ---------- Mensagens montadas (usadas também pelo comando de lembretes)


def reminder_message(registration):
    return build_message(
        registration.user.email,
        f'lembrete: {registration.activity.title}',
        'reminder',
        _registration_context(registration),
        _qr_attachment(registration),
    )


# ---------- Avisos disparados pelas ações do sistema


def notify_registration_confirmed(registration):
    send_on_commit(
        lambda: [
            build_message(
                registration.user.email,
                f'inscrição confirmada em {registration.activity.title}',
                'registration_confirmed',
                _registration_context(registration),
                _qr_attachment(registration),
            )
        ]
    )


def notify_waitlisted(registration):
    send_on_commit(
        lambda: [
            build_message(
                registration.user.email,
                f'você está na lista de espera de {registration.activity.title}',
                'waitlisted',
                {**_registration_context(registration), 'position': registration.waitlist_position()},
            )
        ]
    )


def notify_promoted(registration):
    send_on_commit(
        lambda: [
            build_message(
                registration.user.email,
                f'abriu uma vaga em {registration.activity.title}',
                'promoted',
                _registration_context(registration),
                _qr_attachment(registration),
            )
        ]
    )


def notify_registration_cancelled(registration, was_waitlisted=False):
    send_on_commit(
        lambda: [
            build_message(
                registration.user.email,
                f'inscrição cancelada em {registration.activity.title}',
                'registration_cancelled',
                {**_registration_context(registration), 'was_waitlisted': was_waitlisted},
            )
        ]
    )


def notify_activity_cancelled(activity, registrations):
    registrations = list(registrations)
    send_on_commit(
        lambda: [
            build_message(
                r.user.email,
                f'atividade cancelada: {activity.title}',
                'activity_cancelled',
                _registration_context(r),
            )
            for r in registrations
        ]
    )


def notify_certificates(certificates):
    certificates = list(certificates)
    send_on_commit(
        lambda: [
            build_message(
                c.registration.user.email,
                f'seu certificado de {c.activity_title} está disponível',
                'certificate_issued',
                {
                    **_registration_context(c.registration),
                    'certificate': c,
                    'verify_url': absolute_url(reverse('certificate_verify_code', args=[c.code])),
                },
            )
            for c in certificates
        ]
    )
