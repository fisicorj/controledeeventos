"""Lista, detalhe, edição, cancelamento e revogação das emissões avulsas."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.reauth import require_recent_auth
from core import outbox
from core.models import EmailSettings, OutgoingEmail
from core.pagination import paginate
from core.permissions import can_manage_batch

from .. import zips
from ..batch import (
    active,
    batches_for,
    cancel_batch,
    email_group,
    revoke,
    update_batch,
)
from ..forms import BatchForm, ReasonForm
from .common import FORBIDDEN, closed_redirect, get_managed_batch


@login_required
def batch_list(request):
    if not can_manage_batch(request.user):
        raise PermissionDenied(FORBIDDEN)
    batches = batches_for(request.user)
    query = request.GET.get('q', '').strip()
    if query:
        batches = batches.filter(title__icontains=query)
    batches = batches.annotate(
        total=Count('certificates', filter=Q(certificates__revoked_at__isnull=True)),
        revoked=Count('certificates', filter=Q(certificates__revoked_at__isnull=False)),
    )
    page, querystring = paginate(request, batches, 20)
    context = {'batches': page.object_list, 'page': page, 'querystring': querystring, 'query': query}
    return render(request, 'certificates/batch_list.html', context)


@login_required
def batch_detail(request, pk):
    batch = get_managed_batch(request, pk)
    if batch is None:
        raise PermissionDenied(FORBIDDEN)
    everything = list(batch.certificates.select_related('revoked_by').order_by('participant_name'))
    certificates = [c for c in everything if not c.is_revoked]
    # Lotes grandes (até 3.000 pessoas): a tabela mostra 100 por página, com busca por nome, matrícula ou turma.
    query = request.GET.get('busca', '').strip().lower()
    shown = certificates
    if query:
        shown = [
            c
            for c in certificates
            if query in c.participant_name.lower()
            or query in c.participant_document.lower()
            or query in c.participant_group.lower()
        ]
    page, querystring = paginate(request, shown, 100)
    revoked = sorted((c for c in everything if c.is_revoked), key=lambda c: c.revoked_at, reverse=True)
    with_email = [c for c in certificates if c.participant_email]
    queue_status = outbox.group_status(email_group(batch))[email_group(batch)]
    last_failure = (
        OutgoingEmail.objects.filter(group=email_group(batch), status=OutgoingEmail.Status.FAILED)
        .order_by('-id')
        .values_list('last_error', flat=True)
        .first()
    )
    return render(
        request,
        'certificates/batch_detail.html',
        {
            'batch': batch,
            'certificates': certificates,
            'page': page,
            'querystring': querystring,
            'query': request.GET.get('busca', '').strip(),
            'revoked': revoked,
            'events': batch.events.select_related('actor')[:15],
            'with_email': len(with_email),
            'pending_email': sum(1 for c in with_email if not c.emailed_at),
            'email_enabled': EmailSettings.load().enabled,
            'queue': queue_status,
            'last_failure': last_failure,
            'has_group': any(c.participant_group for c in everything),
            'zip_direct': len(certificates) <= zips.ZIP_DIRECT_LIMIT,
            'zip_export': zips.latest(batch),
        },
    )


@login_required
@require_recent_auth
def batch_edit(request, pk):
    batch = get_managed_batch(request, pk)
    if batch is None:
        raise PermissionDenied(FORBIDDEN)
    if closed := closed_redirect(request, batch):
        return closed
    form = BatchForm(request.POST or None, instance=batch)
    if form.is_valid():
        with transaction.atomic():
            batch = form.save()
            changed = update_batch(batch, request=request)
        messages.success(
            request,
            f'Dados do evento salvos. {changed} certificado{"s" if changed != 1 else ""} '
            f'atualizado{"s" if changed != 1 else ""} (as mudanças ficam registradas na auditoria).',
        )
        return redirect('batch_detail', pk=batch.pk)
    return render(request, 'certificates/batch_edit.html', {'form': form, 'batch': batch})


@login_required
@require_recent_auth
def batch_cancel(request, pk):
    """Cancela a emissão inteira: revoga todos os certificados. Nada é apagado."""
    batch = get_managed_batch(request, pk)
    if batch is None:
        raise PermissionDenied(FORBIDDEN)
    if closed := closed_redirect(request, batch):
        return closed
    total = active(batch).count()
    form = ReasonForm(request.POST if request.method == 'POST' else None)
    if form.is_valid():
        revoked = cancel_batch(batch, form.cleaned_data['reason'], request)
        messages.success(
            request,
            f'Emissão "{batch.title}" cancelada. {len(revoked)} certificado{"s" if len(revoked) != 1 else ""} '
            f'revogado{"s" if len(revoked) != 1 else ""}.',
        )
        return redirect('batch_detail', pk=batch.pk)
    return render(
        request,
        'certificates/revoke.html',
        {
            'form': form,
            'batch': batch,
            'heading': 'Cancelar emissão avulsa',
            'subject': batch.title,
            'consequence': f'{total} certificado{"s" if total != 1 else ""} deste lote '
            f'{"serão revogados" if total != 1 else "será revogado"}.',
            'button': 'Cancelar emissão e revogar certificados',
            'back_url': reverse('batch_detail', args=[batch.pk]),
        },
    )


@login_required
@require_recent_auth
def batch_certificate_revoke(request, pk, code):
    batch = get_managed_batch(request, pk)
    if batch is None:
        raise PermissionDenied(FORBIDDEN)
    certificate = get_object_or_404(batch.certificates, code=code)
    if certificate.is_revoked:
        messages.info(request, 'Este certificado já estava revogado.')
        return redirect('batch_detail', pk=batch.pk)
    form = ReasonForm(request.POST if request.method == 'POST' else None)
    if form.is_valid():
        revoke([certificate], form.cleaned_data['reason'], request)
        messages.success(request, f'Certificado de {certificate.participant_name} ({certificate.code}) revogado.')
        return redirect('batch_detail', pk=batch.pk)
    return render(
        request,
        'certificates/revoke.html',
        {
            'form': form,
            'batch': batch,
            'certificate': certificate,
            'heading': 'Revogar certificado',
            'subject': f'{certificate.participant_name} · {certificate.code}',
            'consequence': 'O certificado continua guardado, mas não pode mais ser baixado nem enviado.',
            'button': 'Revogar certificado',
            'back_url': reverse('batch_detail', args=[batch.pk]),
        },
    )
