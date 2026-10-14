"""Confirmação de e-mail por código de 6 dígitos.

O código vale 15 minutos e aceita até 5 tentativas. O reenvio é limitado (por pessoa e por IP), para o
cadastro não virar uma forma de mandar e-mails em massa pelo servidor da instituição."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core import throttle
from core.models import SiteSettings
from registrations.notifications import build_message, send_messages

from .models import EmailCode

CODE_TTL = timedelta(minutes=15)
MAX_ATTEMPTS = 5
# Por IP o limite é alto: uma turma inteira se cadastra no laboratório, saindo pelo mesmo IP.
SENDS_PER_USER, SENDS_PER_IP, SEND_PERIOD = 3, 60, 3600


def _hash(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def _ip(request):
    from core.http import client_ip

    return client_ip(request) or '-'


def send_code(request, user, email, institutional_id=None):
    """Gera e envia um código novo. Devolve (enviado, mensagem de erro).

    institutional_id: matrícula/CPF digitada junto com a troca de e-mail, gravada só na confirmação."""
    if throttle.blocked(SENDS_PER_USER, SEND_PERIOD, 'code-send-user', user.pk) or throttle.blocked(
        SENDS_PER_IP, SEND_PERIOD, 'code-send-ip', _ip(request)
    ):
        return False, 'Você pediu muitos códigos. Espere uma hora e tente de novo, ou use o último código recebido.'
    throttle.hit(SENDS_PER_USER, SEND_PERIOD, 'code-send-user', user.pk)
    throttle.hit(SENDS_PER_IP, SEND_PERIOD, 'code-send-ip', _ip(request))
    code = f'{secrets.randbelow(10**6):06d}'
    from .models import email_taken

    if email_taken(email, exclude_pk=user.pk):
        # E-mail de outra conta confirmada (SEC-09): para quem pediu, a resposta é a mesma ("enviamos um código"),
        # mas o código não sai. O dono do endereço recebe um aviso, no máximo 3 por dia.
        user.email_codes.filter(used_at__isnull=True).update(used_at=timezone.now())
        EmailCode.objects.create(user=user, email=email, code_hash=_hash(secrets.token_hex(16)))
        _notify_owner(email, _ip(request))
        return True, ''
    previous = pending(user)
    if institutional_id is None and previous and previous.email == email:
        institutional_id = previous.institutional_id  # reenvio: mantém a matrícula digitada na troca
    user.email_codes.filter(used_at__isnull=True).update(used_at=timezone.now())  # o anterior deixa de valer
    EmailCode.objects.create(user=user, email=email, code_hash=_hash(code), institutional_id=institutional_id)
    message = build_message(
        email,
        f'seu código de confirmação: {code}',
        'email_code',
        {'name': user.first_name or user.username, 'code': code, 'minutes': int(CODE_TTL.total_seconds() // 60)},
    )
    if not send_messages([message]):
        return False, 'Não foi possível enviar o e-mail agora. Confira o endereço e tente de novo em instantes.'
    return True, ''


# Avisos de "e-mail em uso": no máximo 3 por dia para o mesmo endereço e 10 por hora saindo do mesmo IP (SEC-16),
# para o cadastro não virar forma de mandar mensagens não pedidas pelo servidor de e-mail da instituição.
NOTICES_PER_EMAIL, NOTICE_PERIOD = 3, 86400
NOTICES_PER_IP, NOTICE_IP_PERIOD = 10, 3600


def _notify_owner(email, ip='-'):
    from django.urls import reverse

    from registrations.notifications import absolute_url

    from .models import User

    owner = User.objects.filter(email__iexact=email, email_verified=True).first()
    if owner is None:
        return
    if throttle.blocked(NOTICES_PER_IP, NOTICE_IP_PERIOD, 'email-in-use-ip', ip):
        return
    if not throttle.hit(NOTICES_PER_EMAIL, NOTICE_PERIOD, 'email-in-use', email):
        return
    throttle.hit(NOTICES_PER_IP, NOTICE_IP_PERIOD, 'email-in-use-ip', ip)
    context = {
        'name': owner.first_name or owner.username,
        'username': owner.username,
        'reset_url': absolute_url(reverse('password_reset')),
    }
    send_messages([build_message(owner.email, 'tentativa de usar o seu e-mail', 'email_in_use', context)])


def pending(user):
    return user.email_codes.filter(used_at__isnull=True, created_at__gte=timezone.now() - CODE_TTL).first()


def confirm(user, typed):
    """Confere o código. Devolve (ok, mensagem). Se ok, o e-mail do código passa a ser o da conta, confirmado,
    e a matrícula é preenchida quando o e-mail é do domínio dos alunos."""
    entry = pending(user)
    if entry is None:
        return False, 'O código expirou ou já foi usado. Peça um novo.'
    if entry.attempts >= MAX_ATTEMPTS:
        return False, 'Muitas tentativas com este código. Peça um novo.'
    entry.attempts += 1
    entry.save(update_fields=['attempts'])
    typed = ''.join(ch for ch in (typed or '') if ch.isdigit())
    if not hmac.compare_digest(_hash(typed), entry.code_hash):
        left = MAX_ATTEMPTS - entry.attempts
        return (
            False,
            f'Código incorreto. {left} tentativa{"s" if left != 1 else ""} restante{"s" if left != 1 else ""}.',
        )
    entry.used_at = timezone.now()
    entry.save(update_fields=['used_at'])
    from .models import email_taken, release_email

    if email_taken(entry.email, exclude_pk=user.pk):
        return False, 'Este e-mail passou a ser usado por outra conta. Use outro endereço.'
    # Outros cadastros não confirmados com o mesmo e-mail saem do caminho quando não têm histórico; os que têm
    # continuam existindo, sem confirmação (o e-mail só é único entre contas confirmadas).
    release_email(entry.email, exclude_pk=user.pk)
    user.email = entry.email
    user.email_verified = True
    fields = ['email', 'email_verified']
    if entry.institutional_id is not None:
        user.institutional_id = entry.institutional_id
        fields.append('institutional_id')
    elif not (user.institutional_id or '').strip():
        student_id = SiteSettings.load().student_id_from_email(entry.email)
        if student_id:  # conta sem matrícula que passou a usar o e-mail institucional
            user.institutional_id = student_id
            fields.append('institutional_id')
    user.save(update_fields=fields)
    return True, ''
