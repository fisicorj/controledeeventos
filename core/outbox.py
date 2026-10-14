"""Fila de e-mails (tabela OutgoingEmail).

Quem envia chama enqueue() dentro da própria transação: se a operação for desfeita, o e-mail também some.
Depois que a transação é gravada, a fila é processada fora da requisição:

- OUTBOX_MODE=thread (padrão): uma linha de execução em segundo plano no próprio servidor começa na hora;
- OUTBOX_MODE=sync (testes): envia ali mesmo, ao gravar a transação;
- OUTBOX_MODE=off: só o comando "python manage.py process_outbox" envia (rodando como serviço ou agendado).

O comando também serve de rede de segurança: se o servidor reiniciar no meio de um lote, o que ficou na fila sai
na próxima execução. Cada e-mail é reservado com SELECT ... FOR UPDATE SKIP LOCKED, então vários processos podem
trabalhar ao mesmo tempo sem mandar a mesma mensagem duas vezes.
"""

import contextlib
import logging
import smtplib
import threading
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.mail import EmailMessage, get_connection
from django.db import connections, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.module_loading import import_string

from .mail_html import with_html
from .models import OutgoingEmail
from .text import single_line

logger = logging.getLogger(__name__)
Status = OutgoingEmail.Status

CLAIM_SIZE = 20
MAX_ATTEMPTS = 5
RETRY_DELAYS = [60, 5 * 60, 15 * 60, 60 * 60]  # segundos até a próxima tentativa
STALE_LOCK = timedelta(minutes=10)  # "enviando" há mais tempo que isso: o processo caiu, volta para a fila

# Anexos são gerados na hora do envio (QR, agenda, PDF do certificado), não guardados na fila.
ATTACHMENT_BUILDERS = {
    'qr': 'registrations.notifications.qr_attachment',
    'ics': 'registrations.notifications.ics_attachment',
    'certificate_pdf': 'certificates.batch.pdf_attachment',
}


class PermanentError(Exception):
    """Não adianta tentar de novo (ex.: o certificado foi revogado antes do envio)."""


# ---------- Entrada na fila


def enqueue(to, subject, body, *, attachments=(), group='', reply_to='', certificate=None, request=None):
    actor = getattr(request, 'user', None)
    email = OutgoingEmail.objects.create(
        to=to,
        subject=single_line(subject)[:300],  # quebra de linha no assunto faria o envio falhar sempre
        body=body,
        reply_to=reply_to or '',
        attachments=list(attachments),
        group=group[:60],
        certificate_pk=certificate.pk if certificate is not None else None,
        actor_pk=actor.pk if actor is not None and actor.is_authenticated else None,
        ip=_ip(request),
    )
    _schedule_kick()
    return email


def _ip(request):
    if request is None:
        return None
    from core.http import client_ip

    return client_ip(request) or None


def _schedule_kick():
    """Processa a fila quando a transação atual for gravada. Chamadas repetidas são baratas: com a linha de
    execução já rodando, kick() só pede mais uma volta."""
    transaction.on_commit(kick)


def kick():
    mode = getattr(settings, 'OUTBOX_MODE', 'thread')
    if mode == 'sync':
        process()
    elif mode == 'thread':
        _start_thread()


# ---------- Linha de execução em segundo plano (uma por processo do servidor)

_lock = threading.Lock()
_wake = threading.Event()
_running = False
MAX_WAIT = 300  # segundos: com tentativas agendadas, a linha de execução confere a fila pelo menos nesse intervalo


def _start_thread():
    global _running
    with _lock:
        _wake.set()  # se já está rodando (ou esperando uma nova tentativa), acorda e dá mais uma volta
        if _running:
            return
        _running = True
    threading.Thread(target=_worker, name='outbox', daemon=True).start()


def _worker():
    """Envia a fila; se sobrar tentativa agendada (servidor fora do ar), espera por ela e tenta de novo."""
    global _running
    finished = False
    try:
        while True:
            _wake.clear()
            try:
                process()
            except Exception:
                logger.exception('Erro ao processar a fila de e-mails.')
            wait = seconds_until_next()
            connections.close_all()  # não segura conexão com o banco enquanto espera
            if wait is None:
                with _lock:
                    if not _wake.is_set():
                        _running, finished = False, True
                        return
                continue
            _wake.wait(timeout=min(max(wait, 1), MAX_WAIT))
    finally:
        if not finished:
            with _lock:
                _running = False
        connections.close_all()


def seconds_until_next():
    """Segundos até a próxima tentativa agendada, ou None se a fila está vazia."""
    upcoming = (
        OutgoingEmail.objects.filter(status=Status.PENDING)
        .order_by('next_try_at')
        .values_list('next_try_at', flat=True)
    ).first()
    if upcoming is None:
        return None
    return max((upcoming - timezone.now()).total_seconds(), 0)


# ---------- Envio


def process(limit=None):
    """Envia o que está na fila. Devolve (enviados, falhas)."""
    sent = failed = 0
    while limit is None or sent + failed < limit:
        rows = _claim(CLAIM_SIZE if limit is None else min(CLAIM_SIZE, limit - sent - failed))
        if not rows:
            break
        ok, bad, stop = _send(rows)
        sent, failed = sent + ok, failed + bad
        if stop:  # servidor de e-mail fora do ar: o resto espera a próxima tentativa
            break
    return sent, failed


def _claim(size):
    now = timezone.now()
    ready = Q(status=Status.PENDING, next_try_at__lte=now) | Q(status=Status.SENDING, locked_at__lt=now - STALE_LOCK)
    with transaction.atomic():
        rows = list(OutgoingEmail.objects.select_for_update(skip_locked=True).filter(ready).order_by('id')[:size])
        OutgoingEmail.objects.filter(pk__in=[r.pk for r in rows]).update(status=Status.SENDING, locked_at=now)
    return rows


def _build(row):
    message = EmailMessage(row.subject, row.body, to=[row.to], reply_to=[row.reply_to] if row.reply_to else None)
    for spec in row.attachments or []:
        builder = import_string(ATTACHMENT_BUILDERS[spec['kind']])
        filename, content, mimetype = builder(spec['id'])
        message.attach(filename, content, mimetype)
    return with_html(message)


def _send(rows):
    """Devolve (enviados, falhas, parar). Se nem a conexão com o servidor abre, as mensagens restantes voltam
    para a fila sem gastar tentativa e o processamento para."""
    sent = failed = 0
    connection = None
    try:
        for index, row in enumerate(rows):
            try:
                message = _build(row)
            except Exception as exc:
                _failed(row, exc, permanent=isinstance(exc, (PermanentError, ObjectDoesNotExist, KeyError)))
                failed += 1
                continue
            for attempt in (1, 2):
                if connection is None:
                    try:
                        connection = get_connection()
                        connection.open()
                    except Exception as exc:
                        connection = None
                        _failed(row, exc)
                        _release(rows[index + 1 :])
                        return sent, failed + 1, True
                try:
                    if connection.send_messages([message]):
                        _sent(row)
                        sent += 1
                    else:
                        _failed(row, 'O servidor de e-mail não aceitou a mensagem.')
                        failed += 1
                    break
                except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError) as exc:
                    _close(connection)  # a conexão caiu: reconecta e tenta esta mensagem mais uma vez
                    connection = None
                    if attempt == 2:
                        _failed(row, exc)
                        failed += 1
                except smtplib.SMTPRecipientsRefused as exc:
                    _failed(row, exc, permanent=True)  # endereço recusado: não adianta insistir
                    failed += 1
                    break
                except Exception as exc:
                    _close(connection)
                    connection = None
                    _failed(row, exc)
                    failed += 1
                    break
    finally:
        _close(connection)
    return sent, failed, False


def _release(rows):
    if rows:
        OutgoingEmail.objects.filter(pk__in=[r.pk for r in rows]).update(
            status=Status.PENDING, locked_at=None, next_try_at=timezone.now() + timedelta(seconds=RETRY_DELAYS[0])
        )


def _close(connection):
    if connection is not None:
        with contextlib.suppress(Exception):  # conexão já caída: nada a fazer
            connection.close()


def _sent(row):
    now = timezone.now()
    OutgoingEmail.objects.filter(pk=row.pk).update(
        status=Status.SENT, sent_at=now, attempts=row.attempts + 1, locked_at=None, last_error=''
    )
    if row.certificate_pk:
        try:
            _certificate_sent(row, now)
        except Exception:
            logger.exception('E-mail %s enviado, mas não foi possível registrar o envio do certificado.', row.pk)


def _certificate_sent(row, when):
    from accounts.models import User
    from certificates import audit
    from certificates.models import Certificate

    certificate = Certificate.objects.filter(pk=row.certificate_pk).first()
    if certificate is None:
        return
    Certificate.objects.filter(pk=certificate.pk).update(emailed_at=when)
    actor = User.objects.filter(pk=row.actor_pk).first() if row.actor_pk else None
    audit.record(audit.Action.EMAILED, [certificate], actor=actor, ip=row.ip, details={'recipient': row.to})


def _failed(row, error, permanent=False):
    attempts = row.attempts + 1
    final = permanent or attempts >= MAX_ATTEMPTS
    delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]
    text = f'{type(error).__name__}: {error}' if isinstance(error, Exception) else str(error)
    OutgoingEmail.objects.filter(pk=row.pk).update(
        status=Status.FAILED if final else Status.PENDING,
        attempts=attempts,
        next_try_at=timezone.now() + timedelta(seconds=delay),
        locked_at=None,
        last_error=text[:500],
    )
    logger.warning('Falha ao enviar e-mail %s para %s (tentativa %s): %s', row.pk, row.to, attempts, text)


# ---------- Consultas para as telas


def group_status(*groups):
    """{grupo: {'pending': n, 'sent': n, 'failed': n}} (pending inclui o que está sendo enviado agora)."""
    out = {g: {'pending': 0, 'sent': 0, 'failed': 0} for g in groups}
    rows = OutgoingEmail.objects.filter(group__in=groups).values('group', 'status').annotate(n=Count('id'))
    for row in rows:
        key = {Status.SENT: 'sent', Status.FAILED: 'failed'}.get(row['status'], 'pending')
        out[row['group']][key] += row['n']
    return out


# ---------- Retenção (LGPD: guardar só o necessário e pelo tempo necessário)

# O que sai da mensagem depois do prazo. Ficam grupo, situação e datas, que alimentam o andamento dos envios
# (ex.: "48 enviados" no lote avulso) sem guardar destinatário nem conteúdo.
REDACTED = {'to': '', 'subject': '', 'body': '', 'reply_to': '', 'attachments': [], 'ip': None, 'last_error': ''}


def redact_sent(days=None):
    """Apaga destinatário e conteúdo dos e-mails enviados há mais de `days` dias, e dos que falharam de vez e foram
    criados há mais de `days` dias (SEC-15 da 2ª auditoria). Devolve quantos.

    `days` vem de OUTBOX_RETENTION_DAYS; 0 desliga a limpeza."""
    days = settings.OUTBOX_RETENTION_DAYS if days is None else days
    if days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    old = Q(status=Status.SENT, sent_at__lt=cutoff) | Q(status=Status.FAILED, created_at__lt=cutoff)
    return OutgoingEmail.objects.filter(old).exclude(to='').update(**REDACTED)


def forget_address(address):
    """Exclusão de conta: tira da fila o que não saiu e apaga destinatário e conteúdo do que já saiu."""
    rows = OutgoingEmail.objects.filter(to__iexact=address)
    rows.filter(status__in=[Status.PENDING, Status.FAILED]).delete()
    return rows.filter(status__in=[Status.SENT, Status.SENDING]).update(**REDACTED)


def retryable_failures():
    """Falhas recentes o bastante para ainda fazer sentido reenviar (OUTBOX_RETRY_DAYS, padrão 3 dias).

    Um aviso de vaga liberada, um lembrete ou um código de confirmação de semanas atrás só confundiria quem recebe."""
    since = timezone.now() - timedelta(days=settings.OUTBOX_RETRY_DAYS)
    return OutgoingEmail.objects.filter(status=Status.FAILED, created_at__gte=since).exclude(to='')


def retry_failed():
    """Devolve para a fila os e-mails que falharam de vez nos últimos OUTBOX_RETRY_DAYS dias. Devolve quantos."""
    count = retryable_failures().update(status=Status.PENDING, attempts=0, next_try_at=timezone.now(), last_error='')
    if count:
        _schedule_kick()
    return count
