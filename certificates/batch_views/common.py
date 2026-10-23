"""Peças comuns das telas da emissão avulsa."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.utils.text import slugify

from core.permissions import can_manage_batch

from ..models import CertificateBatch

SESSION_KEY = 'certificate_import'
FORBIDDEN = 'Acesso restrito a organizadores e administradores.'


def get_managed_batch(request, pk):
    batch = get_object_or_404(CertificateBatch.objects.select_related('created_by'), pk=pk)
    return batch if can_manage_batch(request.user, batch) else None


def closed_redirect(request, batch):
    """Lote cancelado não aceita mais alterações. Devolve o redirecionamento, se for o caso."""
    if batch.is_cancelled:
        messages.error(request, 'Esta emissão foi cancelada e não pode mais ser alterada.')
        return redirect('batch_detail', pk=batch.pk)
    return None


def batch_filename(batch, suffix):
    return f'{slugify(batch.title)[:50] or "certificados"}-{suffix}'
