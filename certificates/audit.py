"""Registro de auditoria dos certificados (tabela CertificateEvent, só de inclusão)."""

from django.utils import timezone

from .models import CertificateEvent

Action = CertificateEvent.Action


def _ip(request):
    if request is None:
        return None
    from core.http import client_ip

    ip = client_ip(request)
    return ip or None


def _actor_name(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return ''
    full = user.get_full_name()
    return f'{full} ({user.username})' if full else user.username


def _event(action, certificate, actor, ip, details, when):
    return CertificateEvent(
        created_at=when,
        action=action,
        certificate=certificate,
        batch_id=certificate.batch_id,
        actor=actor if actor is not None and actor.is_authenticated else None,
        actor_name=_actor_name(actor),
        ip=ip,
        code=certificate.code,
        participant_name=certificate.participant_name,
        participant_document=certificate.participant_document,
        activity_title=certificate.activity_title,
        details=details or {},
    )


def record(action, certificates, *, actor=None, request=None, details=None, ip=None):
    """Grava um evento para cada certificado. `details` pode ser um dicionário (igual para todos)
    ou uma função que recebe o certificado e devolve o dicionário. Fora de uma requisição (envio pela fila),
    quem fez e o IP vêm em `actor` e `ip`."""
    if request is not None and actor is None:
        actor = request.user
    ip, when = _ip(request) if request is not None else ip, timezone.now()
    events = [_event(action, c, actor, ip, details(c) if callable(details) else details, when) for c in certificates]
    return CertificateEvent.objects.bulk_create(events)


def snapshot(certificate, fields):
    return {field: getattr(certificate, field) for field in fields}


def _show(value):
    if value is None:
        return ''
    if hasattr(value, 'tzinfo') and hasattr(value, 'hour'):
        value = timezone.localtime(value).date()
    if hasattr(value, 'strftime'):
        return value.strftime('%d/%m/%Y')
    return str(value)


def changes(before, after):
    """Diferenças legíveis entre duas fotos dos campos: {campo: [antes, depois]}."""
    out = {}
    for field, old in before.items():
        old_s, new_s = _show(old), _show(after.get(field))
        if old_s != new_s:
            out[field] = [old_s, new_s]
    return out
