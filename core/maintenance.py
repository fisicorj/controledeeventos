"""Faxina diária do banco e do disco (BD-02). Roda pelo serviço worker ou pelo comando run_maintenance."""

from datetime import timedelta

from django.utils import timezone

EMAIL_CODE_DAYS = 7  # códigos de confirmação valem 15 minutos; depois de uma semana não servem nem para histórico
THROTTLE_DAYS = 2  # contadores de tentativas têm janelas de no máximo 1 hora
UNVERIFIED_ACCOUNT_DAYS = 7  # cadastro que nunca confirmou o e-mail


def clear_sessions():
    """Sessões vencidas (o Django não apaga sozinho; equivale ao comando clearsessions), e o registro de quais
    sessões são de quem (accounts/sessions.py) acompanha."""
    from django.contrib.sessions.models import Session

    from accounts.sessions import forget_closed

    removed = Session.objects.filter(expire_date__lt=timezone.now()).delete()[0]
    forget_closed()
    return removed


def purge_email_codes():
    from accounts.models import EmailCode

    return EmailCode.objects.filter(created_at__lt=timezone.now() - timedelta(days=EMAIL_CODE_DAYS)).delete()[0]


def purge_throttle():
    from .models import Throttle

    return Throttle.objects.filter(window_start__lt=timezone.now() - timedelta(days=THROTTLE_DAYS)).delete()[0]


def purge_unverified_accounts():
    """Cadastros de participante que nunca confirmaram o e-mail em 7 dias, sem nenhum histórico ligado.
    Inclui as tentativas de cadastro com o e-mail de outra pessoa (o código nunca chega a quem tentou)."""
    from django.db import transaction
    from django.db.models import ProtectedError

    from accounts.models import User

    # Só cadastros abandonados: conta ativa (a exclusão pelo participante desativa e anonimiza, e essa fica),
    # sem nenhuma inscrição. O PROTECT nas relações é a segunda barreira (QUA-06 da 2ª auditoria).
    stale = User.objects.filter(
        is_active=True,
        registrations__isnull=True,
        email_verified=False,
        role=User.Role.PARTICIPANT,
        is_staff=False,
        is_superuser=False,
        date_joined__lt=timezone.now() - timedelta(days=UNVERIFIED_ACCOUNT_DAYS),
    )
    removed = 0
    for user in stale:
        try:
            with transaction.atomic():
                user.delete()
            removed += 1
        except ProtectedError:
            continue
    return removed


def clean_image_cache():
    from certificates.pdf import clean_image_cache as clean

    return clean()


def export_audit():
    """Só quando AUDIT_EXPORT_DIR estiver definido."""
    from django.conf import settings

    from . import audit_export

    if not settings.AUDIT_EXPORT_DIR:
        return 'desligada (AUDIT_EXPORT_DIR em branco)'
    results = audit_export.export_all(settings.AUDIT_EXPORT_DIR)
    return sum(count for _, count in results.values())


def purge_invites():
    from accounts.invites import purge_old

    return purge_old()


def purge_reviewer_invites():
    from submissions.invites import purge_old

    return purge_old()


def clean_zip_exports():
    from certificates import zips

    return zips.cleanup()


def redact_emails():
    from . import outbox

    return outbox.redact_sent()


def reencrypt_secrets():
    from . import crypto

    return crypto.reencrypt_all()


TASKS = [
    ('Segredos regravados com a chave atual', reencrypt_secrets),
    ('Sessões vencidas apagadas', clear_sessions),
    ('Códigos de confirmação antigos apagados', purge_email_codes),
    ('Contadores de tentativas antigos apagados', purge_throttle),
    ('Cadastros não confirmados apagados', purge_unverified_accounts),
    ('Cópias de imagens sem uso apagadas', clean_image_cache),
    ('E-mails antigos com o conteúdo apagado', redact_emails),
    ('ZIPs de certificados vencidos apagados', clean_zip_exports),
    ('Convites de equipe antigos apagados', purge_invites),
    ('Convites de avaliador antigos apagados', purge_reviewer_invites),
    ('Registros de auditoria exportados', export_audit),
]


FAILED_PREFIX = 'falhou: '


def run(log=None, source='comando', record=None):
    """Roda cada tarefa; a falha de uma não impede as outras. Devolve {descrição: quantidade ou erro}.
    Cada execução fica registrada (MaintenanceRun) para a tela Configurações > Manutenção."""
    from .models import MaintenanceRun

    if record is None:
        record = MaintenanceRun.objects.create(source=source)
    results = {}
    for label, task in TASKS:
        try:
            results[label] = task()
        except Exception as exc:
            results[label] = f'{FAILED_PREFIX}{exc}'[:500]
        if log:
            log(f'{label}: {results[label]}')
    record.results = {label: value if isinstance(value, int) else str(value) for label, value in results.items()}
    record.failed = any(str(value).startswith(FAILED_PREFIX) for value in results.values())
    record.finished_at = timezone.now()
    record.save(update_fields=['results', 'failed', 'finished_at'])
    old = MaintenanceRun.objects.values_list('pk', flat=True)[MaintenanceRun.KEEP :]
    MaintenanceRun.objects.filter(pk__in=list(old)).delete()
    return results


# ---------- "Rodar agora" pela tela (DES-05 da 3ª auditoria): a faxina sai da requisição


def request_run(user):
    """Coloca uma faxina na fila (se já não houver uma esperando) e chama quem vai rodá-la."""
    from django.db import transaction

    from .models import MaintenanceRun

    waiting = MaintenanceRun.objects.filter(queued=True).first()
    if waiting:
        return waiting
    record = MaintenanceRun.objects.create(source=f'manual ({user.username})'[:200], queued=True)
    transaction.on_commit(_kick)
    return record


def _kick():
    import threading

    from django.conf import settings

    mode = getattr(settings, 'OUTBOX_MODE', 'thread')
    if mode == 'sync':
        run_queued()
    elif mode == 'thread':
        threading.Thread(target=_thread_run, name='maintenance', daemon=True).start()


def _thread_run():
    from django.db import close_old_connections

    try:
        run_queued()
    finally:
        close_old_connections()


def run_queued(log=None):
    """Roda as faxinas pedidas pela tela. Chamado pelo worker a cada volta; só um processo pega cada pedido."""
    from django.db import transaction

    from .models import MaintenanceRun

    done = 0
    while True:
        with transaction.atomic():
            record = MaintenanceRun.objects.select_for_update(skip_locked=True).filter(queued=True).first()
            if record is None:
                return done
            record.queued, record.started_at = False, timezone.now()
            record.save(update_fields=['queued', 'started_at'])
        run(log=log, record=record)
        done += 1
