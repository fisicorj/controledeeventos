"""ZIP de lotes grandes em segundo plano (DES-02).

Até ZIP_DIRECT_LIMIT certificados, o ZIP sai na própria requisição, como sempre. Acima disso, montar milhares de
PDFs passaria do tempo limite do servidor: o pedido entra na fila (ZipExport) e o worker monta o arquivo, que
fica em private_media/zips/ por ZIP_KEEP_HOURS horas. Sem worker, segue o mesmo OUTBOX_MODE da fila de e-mails:
thread (padrão) monta numa linha de execução do próprio servidor; sync (testes) monta ao gravar o pedido."""

import threading
from datetime import timedelta

from django.conf import settings
from django.core.files import File
from django.db import close_old_connections, transaction
from django.utils import timezone

from .models import ZipExport

ZIP_DIRECT_LIMIT = 200
ZIP_KEEP_HOURS = 24
STUCK_AFTER = timedelta(hours=1)  # gerando há mais que isso: o processo caiu no meio, volta para a fila


def request_export(batch, user):
    """Pede o ZIP do lote. Se já há um pedido na fila ou em andamento, devolve ele em vez de criar outro."""
    _requeue_stuck()
    open_export = batch.zip_exports.filter(status__in=[ZipExport.Status.PENDING, ZipExport.Status.RUNNING]).first()
    if open_export:
        return open_export
    export = ZipExport.objects.create(batch=batch, requested_by=user)
    transaction.on_commit(kick)
    return export


def latest(batch):
    """Último pedido ainda útil (na fila, gerando, pronto e dentro do prazo, ou que falhou)."""
    cutoff = timezone.now() - timedelta(hours=ZIP_KEEP_HOURS)
    return batch.zip_exports.filter(created_at__gte=cutoff).first()


def kick():
    mode = getattr(settings, 'OUTBOX_MODE', 'thread')
    if mode == 'sync':
        process()
    elif mode == 'thread':
        threading.Thread(target=_thread_worker, name='zip-export', daemon=True).start()


def _thread_worker():
    try:
        process()
    finally:
        close_old_connections()


def _claim():
    with transaction.atomic():
        export = (
            ZipExport.objects.select_for_update(skip_locked=True)
            .filter(status=ZipExport.Status.PENDING)
            .order_by('created_at')
            .first()
        )
        if export is None:
            return None
        export.status, export.started_at = ZipExport.Status.RUNNING, timezone.now()
        export.save(update_fields=['status', 'started_at'])
        return export


def process(limit=None):
    """Monta os ZIPs da fila (no máximo `limit`, se informado). Vários processos podem rodar juntos: cada pedido
    é pego por um só."""
    from .batch import build_zip

    done = 0
    while (limit is None or done < limit) and (export := _claim()) is not None:
        try:
            spool, count = build_zip(export.batch, actor=export.requested_by, with_count=True)
            with spool:
                export.file.save(f'lote-{export.batch_id}-{export.pk}.zip', File(spool), save=False)
            export.status, export.count = ZipExport.Status.READY, count
        except Exception as exc:  # o pedido falha, a fila continua
            export.status, export.error = ZipExport.Status.FAILED, str(exc)[:300]
        export.finished_at = timezone.now()
        if not ZipExport.objects.filter(pk=export.pk).exists():  # o lote mudou enquanto gerava (discard)
            if export.file:
                export.file.delete(save=False)
            continue
        export.save(update_fields=['status', 'file', 'count', 'error', 'finished_at'])
        done += 1
    return done


def discard(batch_ids):
    """O lote mudou (participantes, dados do evento, revogação): os ZIPs prontos ficaram desatualizados e saem.
    Os arquivos são apagados só depois de gravada a transação."""
    exports = list(ZipExport.objects.filter(batch_id__in=set(batch_ids)))
    if not exports:
        return
    files = [export.file for export in exports if export.file]
    ZipExport.objects.filter(pk__in=[export.pk for export in exports]).delete()
    transaction.on_commit(lambda: [f.delete(save=False) for f in files])


def _requeue_stuck():
    ZipExport.objects.filter(status=ZipExport.Status.RUNNING, started_at__lt=timezone.now() - STUCK_AFTER).update(
        status=ZipExport.Status.PENDING, started_at=None
    )


def cleanup():
    """Apaga os ZIPs vencidos (arquivo e registro) e devolve à fila os que travaram no meio."""
    now = timezone.now()
    _requeue_stuck()
    removed = 0
    for export in ZipExport.objects.filter(created_at__lt=now - timedelta(hours=ZIP_KEEP_HOURS)).exclude(
        status__in=[ZipExport.Status.PENDING, ZipExport.Status.RUNNING]
    ):
        if export.file:
            export.file.delete(save=False)
        export.delete()
        removed += 1
    return removed
