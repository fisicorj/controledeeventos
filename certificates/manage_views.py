"""Revogar e corrigir certificados emitidos (de atividade ou de emissão avulsa). Tudo fica na auditoria."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.reauth import require_recent_auth
from core.permissions import can_manage, can_manage_batch

from . import audit
from .batch import revoke
from .forms import CorrectionForm, ReasonForm
from .models import Certificate

FIELDS = ['participant_name', 'participant_document']


def _load(request, code):
    """Certificado que o usuário pode gerenciar e o endereço para onde voltar."""
    certificate = get_object_or_404(
        Certificate.objects.select_related('registration__activity', 'registration__user', 'batch'), code=code
    )
    if certificate.batch_id:
        allowed = can_manage_batch(request.user, certificate.batch)
        back = reverse('batch_detail', args=[certificate.batch_id])
    else:
        allowed = can_manage(request.user, certificate.registration.activity)
        back = reverse('activity_manage', args=[certificate.registration.activity_id])
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    return certificate, back


@login_required
@require_recent_auth
def certificate_revoke(request, code):
    certificate, back = _load(request, code)
    if certificate.is_revoked:
        messages.info(request, 'Este certificado já estava revogado.')
        return redirect(back)
    form = ReasonForm(request.POST if request.method == 'POST' else None)
    if form.is_valid():
        revoke([certificate], form.cleaned_data['reason'], request)
        messages.success(request, f'Certificado de {certificate.participant_name} ({certificate.code}) revogado.')
        return redirect(back)
    consequence = 'O certificado continua guardado, mas não pode mais ser baixado.'
    if certificate.registration_id:
        consequence += ' Ele não é emitido de novo automaticamente para esta inscrição.'
    return render(
        request,
        'certificates/revoke.html',
        {
            'form': form,
            'heading': 'Revogar certificado',
            'subject': f'{certificate.participant_name} · {certificate.code} · {certificate.activity_title}',
            'consequence': consequence,
            'button': 'Revogar certificado',
            'back_url': back,
        },
    )


@login_required
def certificate_correct(request, code):
    certificate, back = _load(request, code)
    if certificate.is_revoked:
        messages.error(request, 'Certificado revogado não pode ser corrigido.')
        return redirect(back)
    if certificate.batch_id and certificate.batch.is_cancelled:
        messages.error(request, 'Esta emissão foi cancelada e não pode mais ser alterada.')
        return redirect(back)
    profile = None
    if certificate.registration_id:
        user = certificate.registration.user
        profile = {
            'participant_name': user.get_full_name() or user.username,
            'participant_document': user.institutional_id,
        }
    # Para certificado de atividade, o formulário já vem com os dados atuais do cadastro (o caso mais comum:
    # a pessoa corrigiu o nome em "Meus dados" e pede o certificado de novo).
    initial = profile or {f: getattr(certificate, f) for f in FIELDS}
    form = CorrectionForm(request.POST or None, initial=initial)
    if form.is_valid():
        new = {f: form.cleaned_data[f] for f in FIELDS}
        diff = audit.changes(audit.snapshot(certificate, FIELDS), new)
        duplicate = (
            certificate.batch_id
            and new['participant_document']
            and certificate.batch.certificates.filter(
                participant_document=new['participant_document'], revoked_at__isnull=True
            )
            .exclude(pk=certificate.pk)
            .exists()
        )
        if duplicate:
            form.add_error('participant_document', 'Outro certificado válido deste lote já usa esta matrícula.')
        elif not diff:
            messages.info(request, 'Nada mudou: o certificado já tem esses dados.')
            return redirect(back)
        else:
            with transaction.atomic():
                locked = Certificate.objects.select_for_update().get(pk=certificate.pk)
                for field, value in new.items():
                    setattr(locked, field, value)
                locked.save(update_fields=FIELDS)
                audit.record(
                    audit.Action.UPDATED,
                    [locked],
                    request=request,
                    details={'changes': diff, 'reason': form.cleaned_data['reason']},
                )
            messages.success(
                request,
                f'Certificado {certificate.code} corrigido. O código continua o mesmo; '
                'o PDF já sai com os dados novos.',
            )
            return redirect(back)
    return render(
        request,
        'certificates/correct.html',
        {'form': form, 'certificate': certificate, 'profile': profile, 'back_url': back},
    )
