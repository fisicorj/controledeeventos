from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.permissions import can_manage, can_manage_batch

from . import audit
from .batch import signer_for
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


VERIFY_MISSES, VERIFY_PERIOD = 30, 3600  # códigos inexistentes por IP, por hora


def verify_code(request, code):
    from core import throttle
    from core.http import client_ip

    ip = client_ip(request) or '-'
    if throttle.blocked(VERIFY_MISSES, VERIFY_PERIOD, 'verify-miss', ip):
        return render(request, 'certificates/verify.html', {'raw': code, 'throttled': True}, status=429)
    certificate = Certificate.objects.filter(code=normalize_code(code) or '').first()
    if certificate is None:
        throttle.hit(VERIFY_MISSES, VERIFY_PERIOD, 'verify-miss', ip)
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
    own_batch = False
    if certificate.batch_id:
        if not can_manage_batch(request.user, certificate.batch):
            from .history import owns

            own_batch = owns(request.user, certificate)  # emissão avulsa em nome da própria pessoa (Minhas horas)
            if not own_batch:
                raise Http404
        signer = signer_for(certificate.batch)
    else:
        activity = certificate.registration.activity
        own = certificate.registration.user_id == request.user.id
        if not own and not can_manage(request.user, activity):
            raise Http404
        if own and not can_manage(request.user, activity):
            from registrations.survey import needs_survey

            if needs_survey(certificate.registration):
                messages.info(request, 'Responda a pesquisa de satisfação para liberar o seu certificado.')
                return redirect('survey', pk=certificate.registration_id)
        signer = activity.organizer.get_full_name() or activity.organizer.username
    if certificate.is_revoked:
        messages.error(request, 'Este certificado foi revogado e não pode mais ser baixado.')
        return redirect('certificate_verify_code', code=certificate.code)
    pdf = certificate_pdf(certificate, signer_name=signer)
    # Downloads da equipe ficam na auditoria; o do próprio participante, não (ver README).
    if not own_batch and not (certificate.registration_id and certificate.registration.user_id == request.user.id):
        audit.record(audit.Action.DOWNLOADED, [certificate], request=request, details={'via': 'pdf'})
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="certificado-{certificate.code}.pdf"'
    return response


@login_required
def my_hours(request):
    """Horas certificadas da própria pessoa, por semestre, com CSV para entregar à coordenação."""
    from .history import by_semester, certificates_of

    certificates = list(certificates_of(request.user))
    semesters = by_semester(certificates)
    if request.GET.get('formato') == 'csv':
        from core.csvsafe import excel_writer

        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="horas-{request.user.username}.csv"'
        response.write('\ufeff')  # o Excel reconhece acentos
        writer = excel_writer(response)
        writer.writerow(['Semestre', 'Atividade', 'Tipo', 'Data do certificado', 'Carga horária (h)', 'Código'])
        for label, items, _ in semesters:
            for c in items:
                date = c.certificate_date.strftime('%d/%m/%Y')
                writer.writerow([label, c.activity_title, c.activity_type, date, c.workload_hours or '', c.code])
        return response
    if request.GET.get('formato') == 'pdf':
        from .declaration import declaration_pdf

        chosen = request.GET.get('semestre', '')
        selected = [s for s in semesters if s[0] == chosen] if chosen else semesters
        if not selected:
            raise Http404
        suffix = f'-{chosen.replace("/", "-")}' if chosen else ''
        response = HttpResponse(declaration_pdf(request.user, selected), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="declaracao-horas{suffix}.pdf"'
        return response
    total = sum(hours for _, _, hours in semesters)
    return render(
        request,
        'certificates/my_hours.html',
        {'semesters': semesters, 'total': total, 'count': len(certificates)},
    )


# ---------- Vínculo de certificado avulso pelo código (certificates/claim.py)


@login_required
@require_POST
def claim_start(request):
    code = normalize_code(request.POST.get('codigo', ''))
    if not code:
        messages.error(request, 'Código inválido. Ele tem 12 letras e números, como ABCD-EFGH-JKMN.')
        return redirect('my_hours')
    return redirect('certificate_claim', code=code)


@login_required
def claim_confirm(request, code):
    from . import claim

    if claim.blocked(request.user):
        messages.error(request, 'Muitas tentativas. Espere uma hora e tente de novo.')
        return redirect('my_hours')
    certificate, problem = claim.find(request.user, normalize_code(code))
    if problem:
        (messages.info if certificate else messages.error)(request, problem)
        return redirect('my_hours')
    if request.method == 'POST':
        if claim.claim(request.user, certificate, request=request):
            messages.success(request, f'Certificado "{certificate.activity_title}" vinculado às suas horas.')
        else:
            messages.error(request, 'Este certificado acabou de ser vinculado a outra conta. Fale com quem emitiu.')
        return redirect('my_hours')
    return render(request, 'certificates/claim_confirm.html', {'certificate': certificate})


@login_required
@require_POST
def claim_undo(request, code):
    from . import claim

    certificate = get_object_or_404(Certificate, code=code, claimed_by=request.user)
    claim.unclaim(request.user, certificate, request=request)
    messages.success(request, f'O certificado "{certificate.activity_title}" saiu das suas horas.')
    return redirect('my_hours')
