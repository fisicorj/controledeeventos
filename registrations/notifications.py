"""E-mails enviados aos participantes.

Os avisos entram na fila de e-mails (core/outbox.py) dentro da mesma transação da operação: se ela for desfeita,
nada é enviado. O envio acontece fora da requisição, então uma falha ou demora no servidor de e-mail não
atrapalha a operação do usuário, e um lote grande não estoura o tempo limite do servidor.
"""

import contextlib
import logging
import smtplib

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from core import outbox
from core.mail_html import with_html
from core.models import SiteSettings
from core.text import single_line

from .qr import qr_png

logger = logging.getLogger(__name__)


def absolute_url(path):
    return settings.SITE_URL + path


def render_message(subject, template, context):
    """Assunto (com o nome da instituição) e corpo do e-mail."""
    institution = SiteSettings.load().institution_name
    context = {
        'institution': institution,
        'my_registrations_url': absolute_url(reverse('my_registrations')),
        **context,
    }
    return single_line(f'{institution}: {subject}'), render_to_string(f'emails/{template}.txt', context)


def build_message(to, subject, template, context, attachments=()):
    """Mensagem pronta para envio imediato (código de confirmação, lembretes do comando agendado)."""
    full_subject, body = render_message(subject, template, context)
    message = EmailMessage(full_subject, body, to=[to])
    for filename, content, mimetype in attachments:
        message.attach(filename, content, mimetype)
    return with_html(message)


def queue(to, subject, template, context, attachments=(), **options):
    """Coloca o e-mail na fila (anexos como [{"kind": ..., "id": ...}], gerados na hora do envio)."""
    if not to:
        return None
    full_subject, body = render_message(subject, template, context)
    return outbox.enqueue(to, full_subject, body, attachments=attachments, **options)


def send_messages(messages):
    """Envia uma mensagem por vez: um destinatário recusado não impede os outros de receber.
    Se a conexão com o servidor cair no meio, reconecta e continua. Devolve quantas saíram."""
    messages = [m for m in messages if m.to and m.to[0]]
    if not messages:
        return 0
    sent = failed = 0
    connection = None
    try:
        for message in messages:
            for attempt in (1, 2):
                try:
                    if connection is None:
                        connection = get_connection()
                        connection.open()
                    sent += connection.send_messages([message]) or 0
                    break
                except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError) as exc:
                    _close(connection)
                    connection = None
                    if attempt == 2:
                        failed += 1
                        logger.warning('Falha ao enviar para %s: %s', message.to[0], exc)
                except Exception as exc:  # destinatário recusado, mensagem inválida etc.
                    failed += 1
                    logger.warning('Falha ao enviar para %s: %s', message.to[0], exc)
                    break
    finally:
        _close(connection)
    if failed:
        logger.error('%d de %d e-mail(s) não foram enviados.', failed, len(messages))
    return sent


def _close(connection):
    if connection is not None:
        with contextlib.suppress(Exception):  # conexão já caída: nada a fazer
            connection.close()


def _registration_context(registration):
    activity = registration.activity
    return {
        'user': registration.user,
        'name': registration.user.first_name or registration.user.username,
        'activity': activity,
        'activity_url': absolute_url(activity.get_absolute_url()),
    }


def _qr_attachment(registration):
    return [('qrcode-inscricao.png', qr_png(registration), 'image/png')]


def qr_attachment(registration_id):
    from .models import Registration

    return _qr_attachment(Registration.objects.get(pk=registration_id))[0]


def ics_attachment(activity_id):
    """Arquivo .ics, que abre direto na agenda do celular ou do Outlook."""
    from activities import calendar
    from activities.models import Activity

    activity = Activity.objects.get(pk=activity_id)
    content = calendar.ics(activity, absolute_url(activity.get_absolute_url()), SiteSettings.load().institution_name)
    return f'{activity.slug}.ics', content, 'text/calendar'


def _ticket(registration):
    """Anexos do ingresso: QR Code e agenda (.ics)."""
    return [{'kind': 'qr', 'id': registration.pk}, {'kind': 'ics', 'id': registration.activity_id}]


def _survey_context(registration):
    from .survey import needs_survey, survey_open

    activity = registration.activity
    answered = hasattr(registration, 'survey') if registration.pk else False
    return {
        'survey_url': absolute_url(reverse('survey', args=[registration.pk])),
        'survey_invite': survey_open(activity) and not answered,
        'survey_required': needs_survey(registration),
    }


# ---------- Mensagens montadas (usadas também pelo comando de lembretes)


def reminder_message(registration):
    return build_message(
        registration.user.email,
        f'lembrete: {registration.activity.title}',
        'reminder',
        _registration_context(registration),
        _qr_attachment(registration),
    )


# ---------- Avisos disparados pelas ações do sistema (vão para a fila de e-mails)


def notify_registration_confirmed(registration):
    queue(
        registration.user.email,
        f'inscrição confirmada em {registration.activity.title}',
        'registration_confirmed',
        _registration_context(registration),
        _ticket(registration),
        group=f'registration:{registration.pk}',
    )


def notify_waitlisted(registration):
    queue(
        registration.user.email,
        f'você está na lista de espera de {registration.activity.title}',
        'waitlisted',
        {**_registration_context(registration), 'position': registration.waitlist_position()},
        group=f'registration:{registration.pk}',
    )


def notify_promoted(registration):
    queue(
        registration.user.email,
        f'abriu uma vaga em {registration.activity.title}',
        'promoted',
        _registration_context(registration),
        _ticket(registration),
        group=f'registration:{registration.pk}',
    )


def notify_offer(registration):
    """Abriu uma vaga: confirme até o prazo (o QR Code vai na confirmação)."""
    queue(
        registration.user.email,
        f'abriu uma vaga em {registration.activity.title}: confirme até '
        f'{timezone.localtime(registration.offer_expires_at):%d/%m às %H:%M}',
        'waitlist_offer',
        {**_registration_context(registration), 'deadline': registration.offer_expires_at},
        group=f'registration:{registration.pk}',
    )


def notify_offer_expired(registration):
    queue(
        registration.user.email,
        f'o prazo para confirmar a vaga em {registration.activity.title} terminou',
        'waitlist_offer_expired',
        _registration_context(registration),
        group=f'registration:{registration.pk}',
    )


def notify_registration_cancelled(registration, was_waitlisted=False):
    queue(
        registration.user.email,
        f'inscrição cancelada em {registration.activity.title}',
        'registration_cancelled',
        {**_registration_context(registration), 'was_waitlisted': was_waitlisted},
        group=f'registration:{registration.pk}',
    )


def notify_activity_cancelled(activity, registrations):
    for r in registrations:
        queue(
            r.user.email,
            f'atividade cancelada: {activity.title}',
            'activity_cancelled',
            _registration_context(r),
            group=f'activity:{activity.pk}:cancelled',
        )


CHANGE_LABELS = {'start_date': 'Início', 'end_date': 'Término', 'location': 'Local'}


def _show_change(field, value):
    if field == 'location':
        return value or 'a definir'
    return timezone.localtime(value).strftime('%d/%m/%Y às %H:%M')


def notify_activity_changed(activity, registrations, changes):
    """Avisa que a data ou o local mudou. changes: [(campo, antes, depois)]."""
    lines = [
        {'label': CHANGE_LABELS[field], 'before': _show_change(field, old), 'after': _show_change(field, new)}
        for field, old, new in changes
    ]
    for r in registrations:
        queue(
            r.user.email,
            f'mudança em {activity.title}',
            'activity_changed',
            {**_registration_context(r), 'changes': lines, 'waitlisted': r.status == 'WAITLISTED'},
            group=f'activity:{activity.pk}:changed',
        )


def notify_certificates(certificates):
    for c in certificates:
        queue(
            c.registration.user.email,
            f'seu certificado de {c.activity_title} está disponível',
            'certificate_issued',
            {
                **_registration_context(c.registration),
                **_survey_context(c.registration),
                'certificate': c,
                'verify_url': absolute_url(reverse('certificate_verify_code', args=[c.code])),
            },
            group=f'activity:{c.registration.activity_id}:certificates',
        )


def notify_survey(registrations):
    """Convite para a pesquisa de satisfação (para quem não recebeu o e-mail do certificado)."""
    for r in registrations:
        queue(
            r.user.email,
            f'como foi {r.activity.title}? Avalie em 1 minuto',
            'survey_invite',
            {**_registration_context(r), **_survey_context(r)},
            group=f'activity:{r.activity_id}:survey',
        )


def send_announcement(announcement, registrations):
    """Coloca o comunicado na fila para cada inscrito. Devolve quantos e-mails entraram na fila."""
    activity = announcement.activity
    queued = 0
    for r in registrations:
        email = queue(
            r.user.email,
            f'{announcement.subject} ({activity.title})',
            'announcement',
            {**_registration_context(r), 'announcement': announcement},
            reply_to=announcement.sent_by.email,
            group=f'announcement:{announcement.pk}',
        )
        queued += email is not None
    return queued
