from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from activities.views import can_manage

from . import audit
from .batch import can_manage_batch, signer_for
from .models import Certificate
from .pdf import certificate_pdf


def normalize_code(raw):
    clean = ''.join(ch for ch in raw.upper() if ch.isalnum())
    if len(clean) != 12:
        return None
    return f'{clean[:4]}-{clean[4:8]}-{clean[8:]}'


def verify(request):
    """Página pública: qualquer pessoa digita o código e confere se o certificado é autêntico."""
    raw = request.GET.get('codigo', '').strip()
    if raw:
        code = normalize_code(raw)
        if code:
            return redirect('certificate_verify_code', code=code)
        return render(request, 'certificates/verify.html', {'raw': raw, 'not_found': True})
    return render(request, 'certificates/verify.html')


def verify_code(request, code):
    certificate = Certificate.objects.filter(code=normalize_code(code) or '').first()
    return render(
        request, 'certificates/verify.html', {'certificate': certificate, 'raw': code, 'not_found': not certificate}
    )


@login_required
def download(request, code):
    certificate = get_object_or_404(
        Certificate.objects.select_related(
            'registration__activity__organizer', 'registration__user', 'batch__created_by'
        ),
        code=code,
    )
    if certificate.batch_id:
        if not can_manage_batch(request.user, certificate.batch):
            raise Http404
        signer = signer_for(certificate.batch)
    else:
        activity = certificate.registration.activity
        if certificate.registration.user_id != request.user.id and not can_manage(request.user, activity):
            raise Http404
        signer = activity.organizer.get_full_name() or activity.organizer.username
    if certificate.is_revoked:
        messages.error(request, 'Este certificado foi revogado e não pode mais ser baixado.')
        return redirect('certificate_verify_code', code=certificate.code)
    pdf = certificate_pdf(certificate, signer_name=signer)
    # Downloads da equipe ficam na auditoria; o do próprio participante, não (ver README).
    if not (certificate.registration_id and certificate.registration.user_id == request.user.id):
        audit.record(audit.Action.DOWNLOADED, [certificate], request=request, details={'via': 'pdf'})
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="certificado-{certificate.code}.pdf"'
    return response
