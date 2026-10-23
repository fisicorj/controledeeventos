from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.permissions import can_manage, can_manage_batch

from . import audit
from .batch import activity_signer, signer_for
from .models import Certificate, HoursDeclaration
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
    if certificate is None and HoursDeclaration.objects.filter(code=normalize_code(code) or '').exists():
        return redirect('declaration_verify', code=normalize_code(code))  # código de declaração de horas
    if certificate is None:
        throttle.hit(VERIFY_MISSES, VERIFY_PERIOD, 'verify-miss', ip)
    return render(
        request, 'certificates/verify.html', {'certificate': certificate, 'raw': code, 'not_found': not certificate}
    )


@login_required
def download(request, code):
    certificate = get_object_or_404(
        Certificate.objects.select_related(
            'registration__activity__organizer',
            'registration__user',
            'session_enrollment__registration__activity__organizer',
            'session_enrollment__registration__user',
            'speaker__activity__organizer',
            'submission__call__activity__organizer',
            'batch__created_by',
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
    elif certificate.speaker_id or certificate.submission_id:
        from .history import owns

        activity = certificate.source_activity
        own_batch = owns(request.user, certificate)  # palestrante ou autor do trabalho: download sem auditoria
        if not own_batch and not can_manage(request.user, activity):
            raise Http404
        signer = activity_signer(activity)
    else:
        registration = certificate.owner_registration
        activity = registration.activity
        own = registration.user_id == request.user.id
        if not own and not can_manage(request.user, activity):
            raise Http404
        if own and not can_manage(request.user, activity):
            from registrations.survey import needs_survey

            if needs_survey(registration):
                messages.info(request, 'Responda a pesquisa de satisfação para liberar o seu certificado.')
                return redirect('survey', pk=registration.pk)
        signer = activity_signer(activity)
    if certificate.is_revoked:
        messages.error(request, 'Este certificado foi revogado e não pode mais ser baixado.')
        return redirect('certificate_verify_code', code=certificate.code)
    pdf = certificate_pdf(certificate, signer_name=signer)
    # Downloads da equipe ficam na auditoria; o do próprio participante, não (ver README).
    owner = certificate.owner_registration
    if not own_batch and not (owner and owner.user_id == request.user.id):
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
                writer.writerow([label, c.full_title, c.activity_type, date, c.workload_hours or '', c.code])
        return response
    if request.GET.get('formato') == 'pdf':
        from .declaration import TooMany, declaration_pdf, record

        chosen = request.GET.get('semestre', '')
        selected = [s for s in semesters if s[0] == chosen] if chosen else semesters
        if not selected:
            raise Http404
        suffix = f'-{chosen.replace("/", "-")}' if chosen else ''
        try:
            declaration = record(request.user, selected, chosen)
        except TooMany:
            messages.error(request, 'Você já emitiu muitas declarações diferentes hoje. Tente de novo amanhã.')
            return redirect('my_hours')
        pdf = declaration_pdf(request.user, selected, declaration)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="declaracao-horas{suffix}.pdf"'
        return response
    total = sum(hours for _, _, hours in semesters)
    declarations = request.user.hours_declarations.all()[:20]
    return render(
        request,
        'certificates/my_hours.html',
        {'semesters': semesters, 'total': total, 'count': len(certificates), 'declarations': declarations},
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


@login_required
@require_POST
def declaration_cancel(request, code):
    """A própria pessoa cancela uma declaração (ex.: entregue por engano): a página pública deixa de mostrar o
    nome e a lista (SEC-28 da 4ª auditoria)."""
    from django.utils import timezone

    declaration = get_object_or_404(HoursDeclaration, code=code, user=request.user, cancelled_at__isnull=True)
    declaration.cancelled_at = timezone.now()
    declaration.save(update_fields=['cancelled_at'])
    messages.success(
        request, f'Declaração {declaration.code} cancelada: a página de conferência não mostra mais os dados.'
    )
    return redirect('my_hours')


def declaration_verify(request, code):
    """Página pública da declaração de horas: a lista oficial e a situação atual de cada certificado."""
    from core import throttle
    from core.http import client_ip

    ip = client_ip(request) or '-'
    if throttle.blocked(VERIFY_MISSES, VERIFY_PERIOD, 'verify-miss', ip):
        return render(request, 'certificates/verify.html', {'raw': code, 'throttled': True}, status=429)
    declaration = HoursDeclaration.objects.filter(code=normalize_code(code) or '').first()
    if declaration is None:
        throttle.hit(VERIFY_MISSES, VERIFY_PERIOD, 'verify-miss', ip)
        return render(request, 'certificates/verify.html', {'raw': code, 'not_found': True}, status=404)
    if declaration.cancelled_at:
        return render(request, 'certificates/declaration_verify.html', {'declaration': declaration, 'cancelled': True})
    current = {
        c.code: c
        for c in Certificate.objects.filter(code__in=[i['codigo'] for i in declaration.items]).select_related('batch')
    }
    rows = []
    for item in declaration.items:
        certificate = current.get(item['codigo'])
        cancelled = certificate is not None and certificate.batch is not None and certificate.batch.is_cancelled
        valid = certificate is not None and not certificate.is_revoked and not cancelled
        rows.append({**item, 'valid': valid})
    return render(
        request,
        'certificates/declaration_verify.html',
        {
            'declaration': declaration,
            'rows': rows,
            'all_valid': all(r['valid'] for r in rows),
            'any_claimed': any(r['vinculado'] for r in rows),
        },
    )


BULK_LIMIT = 50  # códigos por consulta na verificação em lote


def verify_bulk(request):
    """Verificação em lote (P3 da 4ª auditoria): instituições parceiras colam vários códigos, de certificado ou de
    declaração de horas, e conferem de uma vez. Cada código inexistente conta no mesmo limite por IP da validação
    individual, então a página não serve para testar códigos ao acaso."""
    import re

    from core import throttle
    from core.http import client_ip

    context = {'limit': BULK_LIMIT}
    if request.method != 'POST':
        return render(request, 'certificates/verify_bulk.html', context)
    ip = client_ip(request) or '-'
    raw = request.POST.get('codigos', '')
    tokens = [t for t in re.split(r'[\s,;]+', raw) if t][: BULK_LIMIT + 1]
    context['raw'] = raw
    if len(tokens) > BULK_LIMIT:
        context['error'] = f'Cole no máximo {BULK_LIMIT} códigos por vez.'
        return render(request, 'certificates/verify_bulk.html', context)
    codes = [normalize_code(t) for t in tokens]
    certificates = {
        c.code: c for c in Certificate.objects.filter(code__in=[c for c in codes if c]).select_related('batch')
    }
    declarations = {d.code: d for d in HoursDeclaration.objects.filter(code__in=[c for c in codes if c])}
    rows = []
    for typed, code in zip(tokens, codes, strict=True):
        if throttle.blocked(VERIFY_MISSES, VERIFY_PERIOD, 'verify-miss', ip):
            context['throttled'] = True
            break
        certificate, declaration = certificates.get(code), declarations.get(code)
        if certificate:
            cancelled = certificate.batch is not None and certificate.batch.is_cancelled
            valid = not certificate.is_revoked and not cancelled
            rows.append({'typed': typed, 'code': code, 'kind': 'Certificado', 'valid': valid, 'item': certificate})
        elif declaration:
            valid = declaration.cancelled_at is None
            rows.append({'typed': typed, 'code': code, 'kind': 'Declaração de horas', 'valid': valid,
                         'item': declaration})  # fmt: skip
        else:
            throttle.hit(VERIFY_MISSES, VERIFY_PERIOD, 'verify-miss', ip)
            rows.append({'typed': typed, 'code': code, 'kind': None, 'valid': False, 'item': None})
    context['rows'] = rows
    context['summary'] = {
        'valid': sum(1 for r in rows if r['valid']),
        'invalid': sum(1 for r in rows if r['item'] and not r['valid']),
        'missing': sum(1 for r in rows if not r['item']),
    }
    return render(request, 'certificates/verify_bulk.html', context)
