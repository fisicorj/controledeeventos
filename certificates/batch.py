"""Emissão avulsa: grava os certificados importados da planilha, monta o ZIP e envia por e-mail."""

import tempfile
import zipfile
from datetime import datetime, time

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from core import permissions
from registrations import notifications
from registrations.notifications import absolute_url

from . import audit, zips
from .batch_import import participant_key
from .models import Certificate, CertificateBatch
from .pdf import certificate_pdf


def as_datetime(day):
    # meio-dia local: a data não muda ao converter de fuso
    return timezone.make_aware(datetime.combine(day, time(12)))


def batches_for(user):
    return permissions.batches_for(user, CertificateBatch.objects.select_related('created_by'))


def existing_keys(batch):
    if batch is None:
        return set()
    return {
        participant_key(name, doc)
        for name, doc in active(batch).values_list('participant_name', 'participant_document')
    }


def active(batch):
    """Certificados do lote que valem (os revogados ficam guardados, mas fora de ZIP, e-mail e contagens)."""
    return batch.certificates.filter(revoked_at__isnull=True)


def signer_for(batch):
    """Quem assina os certificados do lote quando a instituição não tem signatário próprio."""
    return batch.signer_name or batch.created_by.get_full_name() or batch.created_by.username


def activity_signer(activity):
    """Idem para os certificados de uma atividade."""
    return activity.signer_name or activity.organizer.get_full_name() or activity.organizer.username


@transaction.atomic
def issue_batch(batch, rows, request=None):
    """Cria os certificados das linhas válidas que ainda não estão no lote. rows: dicionários da prévia."""
    batch = CertificateBatch.objects.select_for_update().get(pk=batch.pk)
    already = existing_keys(batch)
    created = []
    for row in rows:
        key = participant_key(row['name'], row['document'])
        if row['errors'] or key in already:
            continue
        already.add(key)
        created.append(
            Certificate(
                batch=batch,
                participant_name=row['name'],
                participant_document=row['document'],
                participant_group=row['group'],
                participant_email=row['email'],
                activity_title=batch.title,
                activity_type=batch.get_activity_type_display(),
                start_date=as_datetime(batch.start_date),
                end_date=as_datetime(batch.end_date),
                workload_hours=batch.workload_hours,
                document_date=batch.issue_date,
            )
        )
    created = Certificate.objects.bulk_create(created)
    audit.record(audit.Action.ISSUED, created, request=request, details={'source': 'Emissão avulsa'})
    if created:
        zips.discard([batch.pk])
    return created


BATCH_FIELDS = ['activity_title', 'activity_type', 'start_date', 'end_date', 'workload_hours', 'document_date']


@transaction.atomic
def update_batch(batch, request=None):
    """Leva os dados do evento (já salvos no lote) para os certificados válidos e registra o antes e o depois."""
    new = {
        'activity_title': batch.title,
        'activity_type': batch.get_activity_type_display(),
        'start_date': as_datetime(batch.start_date),
        'end_date': as_datetime(batch.end_date),
        'workload_hours': batch.workload_hours,
        'document_date': batch.issue_date,
    }
    changed = []
    for certificate in active(batch).select_for_update():
        diff = audit.changes(audit.snapshot(certificate, BATCH_FIELDS), new)
        if not diff:
            continue
        for field, value in new.items():
            setattr(certificate, field, value)
        certificate.save(update_fields=BATCH_FIELDS)
        changed.append((certificate, diff))
    for certificate, diff in changed:
        audit.record(audit.Action.UPDATED, [certificate], request=request, details={'changes': diff})
    zips.discard([batch.pk])  # a assinatura pode ter mudado mesmo sem diferença nos certificados
    return len(changed)


@transaction.atomic
def revoke(certificates, reason, request=None, extra=None):
    """Revoga (nunca apaga). Certificados já revogados são ignorados. Devolve os revogados agora."""
    now = timezone.now()
    targets = [c for c in certificates if c.revoked_at is None]
    for certificate in targets:
        certificate.revoked_at, certificate.revoked_by, certificate.revoked_reason = now, request.user, reason
        certificate.save(update_fields=['revoked_at', 'revoked_by', 'revoked_reason'])
    audit.record(audit.Action.REVOKED, targets, request=request, details={'reason': reason, **(extra or {})})
    zips.discard([c.batch_id for c in targets if c.batch_id])
    return targets


@transaction.atomic
def cancel_batch(batch, reason, request):
    batch = CertificateBatch.objects.select_for_update().get(pk=batch.pk)
    revoked = revoke(list(active(batch)), reason, request, extra={'batch_cancelled': True})
    batch.cancelled_at, batch.cancelled_by, batch.cancel_reason = timezone.now(), request.user, reason
    batch.save(update_fields=['cancelled_at', 'cancelled_by', 'cancel_reason'])
    return revoked


def pdf_filename(certificate):
    name = slugify(certificate.participant_name)[:60] or 'participante'
    prefix = slugify(certificate.participant_document)
    return f'{prefix}-{name}.pdf' if prefix else f'{name}-{certificate.code}.pdf'


def build_zip(batch, request=None, actor=None, with_count=False):
    """ZIP com um PDF por participante. Fica em arquivo temporário para não estourar a memória em lotes grandes.
    Fora de uma requisição (ZIP montado em segundo plano, certificates/zips.py), quem pediu vem em `actor`."""
    signer = signer_for(batch)
    spool = tempfile.SpooledTemporaryFile(max_size=20 * 1024 * 1024)
    used, included = set(), []
    with zipfile.ZipFile(spool, 'w', zipfile.ZIP_DEFLATED) as archive:
        for certificate in active(batch).order_by('participant_name'):
            filename = pdf_filename(certificate)
            if filename in used:
                filename = filename[:-4] + f'-{certificate.code}.pdf'
            used.add(filename)
            archive.writestr(filename, certificate_pdf(certificate, signer_name=signer))
            included.append(certificate)
    audit.record(audit.Action.DOWNLOADED, included, request=request, actor=actor, details={'via': 'zip'})
    spool.seek(0)
    return (spool, len(included)) if with_count else spool


def pdf_attachment(certificate_id):
    """Anexo gerado na hora do envio pela fila de e-mails (core/outbox.py)."""
    from core.outbox import PermanentError

    certificate = Certificate.objects.select_related(
        'batch__created_by', 'registration__activity', 'session_enrollment__registration__activity'
    ).get(pk=certificate_id)
    if certificate.is_revoked:
        raise PermanentError('O certificado foi revogado antes do envio.')
    if certificate.batch_id:
        signer = signer_for(certificate.batch)
    else:
        signer = activity_signer(certificate.source_activity)
    return pdf_filename(certificate), certificate_pdf(certificate, signer_name=signer), 'application/pdf'


def email_group(batch):
    return f'batch:{batch.pk}'


def queue_emails(batch, resend=False, request=None):
    """Coloca na fila o PDF de quem tem e-mail na planilha. Por padrão só para quem ainda não recebeu.
    Quem já está na fila não entra de novo. Devolve (colocados na fila agora, que já estavam na fila)."""
    from core.models import OutgoingEmail

    certificates = active(batch).exclude(participant_email='')
    if not resend:
        certificates = certificates.filter(emailed_at__isnull=True)
    waiting = set(
        OutgoingEmail.objects.filter(
            group=email_group(batch), status__in=[OutgoingEmail.Status.PENDING, OutgoingEmail.Status.SENDING]
        ).values_list('certificate_pk', flat=True)
    )
    queued = 0
    with transaction.atomic():
        for certificate in certificates.order_by('participant_name'):
            if certificate.pk in waiting:
                continue
            notifications.queue(
                certificate.participant_email,
                f'seu certificado de {certificate.activity_title}',
                'certificate_batch',
                {
                    'name': certificate.participant_name.split(' ')[0],
                    'certificate': certificate,
                    'verify_url': absolute_url(reverse('certificate_verify_code', args=[certificate.code])),
                },
                [{'kind': 'certificate_pdf', 'id': certificate.pk}],
                group=email_group(batch),
                certificate=certificate,
                request=request,
            )
            queued += 1
    return queued, len(waiting)
