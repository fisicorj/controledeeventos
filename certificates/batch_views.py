"""Telas da emissão avulsa de certificados (evento que não foi cadastrado no sistema)."""

import csv
import io
import secrets
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from core.models import EmailSettings

from .batch import (
    active,
    batches_for,
    build_zip,
    can_manage_batch,
    cancel_batch,
    existing_keys,
    issue_batch,
    revoke,
    send_emails,
    update_batch,
)
from .batch_import import SpreadsheetError, parse_participants, read_table
from .forms import BatchForm, ReasonForm, SpreadsheetForm
from .models import CertificateBatch

SESSION_KEY = 'certificate_import'
FORBIDDEN = 'Acesso restrito a organizadores e administradores.'


def _get_batch(request, pk):
    batch = get_object_or_404(CertificateBatch.objects.select_related('created_by'), pk=pk)
    return batch if can_manage_batch(request.user, batch) else None


def _closed(request, batch):
    """Lote cancelado não aceita mais alterações. Devolve o redirecionamento, se for o caso."""
    if batch.is_cancelled:
        messages.error(request, 'Esta emissão foi cancelada e não pode mais ser alterada.')
        return redirect('batch_detail', pk=batch.pk)
    return None


def _filename(batch, suffix):
    return f'{slugify(batch.title)[:50] or "certificados"}-{suffix}'


@login_required
def batch_list(request):
    if not can_manage_batch(request.user):
        return HttpResponseForbidden(FORBIDDEN)
    batches = batches_for(request.user).annotate(
        total=Count('certificates', filter=Q(certificates__revoked_at__isnull=True)),
        revoked=Count('certificates', filter=Q(certificates__revoked_at__isnull=False)),
    )
    return render(request, 'certificates/batch_list.html', {'batches': batches})


def _read_spreadsheet(form, batch=None):
    """Lê e valida a planilha. Devolve as linhas ou None (com o erro anotado no formulário)."""
    try:
        table = read_table(form.cleaned_data['spreadsheet'])
        rows, columns = parse_participants(table, fix_case=form.cleaned_data['fix_case'], existing=existing_keys(batch))
    except SpreadsheetError as exc:
        form.add_error('spreadsheet', str(exc))
        return None, None
    return rows, columns


def _store_preview(request, rows, columns, filename, batch=None, event=None):
    token = secrets.token_urlsafe(16)
    request.session[SESSION_KEY] = {
        'token': token,
        'batch_id': batch.pk if batch else None,
        'event': event,
        'filename': filename,
        'columns': sorted(columns),
        'rows': [row.to_dict() for row in rows],
    }
    return redirect('batch_preview')


@login_required
def batch_new(request):
    if not can_manage_batch(request.user):
        return HttpResponseForbidden(FORBIDDEN)
    # Voltando da prévia ("Enviar outra planilha"), o formulário vem preenchido com o evento digitado.
    previous = request.session.get(SESSION_KEY) or {}
    initial = previous.get('event') if not previous.get('batch_id') else None
    event_form = BatchForm(request.POST or None, initial=initial)
    sheet_form = SpreadsheetForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and event_form.is_valid() and sheet_form.is_valid():
        rows, columns = _read_spreadsheet(sheet_form)
        if rows is not None:
            event = {
                key: value.isoformat() if isinstance(value, date) else value
                for key, value in event_form.cleaned_data.items()
            }
            return _store_preview(request, rows, columns, sheet_form.cleaned_data['spreadsheet'].name, event=event)
    return render(
        request, 'certificates/batch_form.html', {'event_form': event_form, 'sheet_form': sheet_form, 'batch': None}
    )


@login_required
def batch_add(request, pk):
    batch = _get_batch(request, pk)
    if batch is None:
        return HttpResponseForbidden(FORBIDDEN)
    if closed := _closed(request, batch):
        return closed
    sheet_form = SpreadsheetForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and sheet_form.is_valid():
        rows, columns = _read_spreadsheet(sheet_form, batch)
        if rows is not None:
            return _store_preview(request, rows, columns, sheet_form.cleaned_data['spreadsheet'].name, batch=batch)
    return render(request, 'certificates/batch_form.html', {'sheet_form': sheet_form, 'batch': batch})


@login_required
def batch_preview(request):
    if not can_manage_batch(request.user):
        return HttpResponseForbidden(FORBIDDEN)
    data = request.session.get(SESSION_KEY)
    if not data:
        messages.info(request, 'Envie a planilha para ver a prévia.')
        return redirect('batch_list')
    batch = None
    if data['batch_id']:
        batch = _get_batch(request, data['batch_id'])
        if batch is None:
            return HttpResponseForbidden(FORBIDDEN)
        if closed := _closed(request, batch):
            request.session.pop(SESSION_KEY, None)
            return closed

    rows = data['rows']
    valid = [r for r in rows if not r['errors']]

    if request.method == 'POST':
        if request.POST.get('token') != data['token']:
            messages.error(request, 'Esta prévia já foi usada ou expirou. Envie a planilha de novo.')
            return redirect('batch_list')
        if not valid:
            messages.error(request, 'Nenhuma linha válida para emitir.')
            return redirect('batch_preview')
        with transaction.atomic():
            if batch is None:
                event = data['event']
                batch = CertificateBatch.objects.create(
                    title=event['title'],
                    activity_type=event['activity_type'],
                    start_date=date.fromisoformat(event['start_date']),
                    end_date=date.fromisoformat(event['end_date']),
                    workload_hours=event['workload_hours'],
                    issue_date=date.fromisoformat(event['issue_date']),
                    created_by=request.user,
                )
            created = issue_batch(batch, rows, request=request)
        request.session.pop(SESSION_KEY, None)
        skipped = len(rows) - len(created)
        text = (
            f'{len(created)} certificado{"s" if len(created) != 1 else ""} emitido{"s" if len(created) != 1 else ""}.'
        )
        if skipped:
            plural = skipped != 1
            text += f' {skipped} linha{"s" if plural else ""} com problema {"ficaram" if plural else "ficou"} de fora.'
        messages.success(request, text)
        return redirect('batch_detail', pk=batch.pk)

    context = {
        'data': data,
        'batch': batch,
        'rows': rows,
        'valid_count': len(valid),
        'error_count': len(rows) - len(valid),
        'warning_count': sum(1 for r in valid if r['warnings']),
        'renamed_count': sum(1 for r in rows if r['name'] != r['original_name']),
        'has_email': 'email' in data['columns'],
        'has_group': 'group' in data['columns'],
        'has_document': 'document' in data['columns'],
    }
    if batch is None:
        event = data['event']
        context['event'] = {
            **event,
            'start': date.fromisoformat(event['start_date']),
            'end': date.fromisoformat(event['end_date']),
            'issue': date.fromisoformat(event['issue_date']),
            'type_label': dict(CertificateBatch._meta.get_field('activity_type').choices)[event['activity_type']],
        }
    return render(request, 'certificates/batch_preview.html', context)


@login_required
def batch_detail(request, pk):
    batch = _get_batch(request, pk)
    if batch is None:
        return HttpResponseForbidden(FORBIDDEN)
    everything = list(batch.certificates.select_related('revoked_by').order_by('participant_name'))
    certificates = [c for c in everything if not c.is_revoked]
    revoked = sorted((c for c in everything if c.is_revoked), key=lambda c: c.revoked_at, reverse=True)
    with_email = [c for c in certificates if c.participant_email]
    return render(
        request,
        'certificates/batch_detail.html',
        {
            'batch': batch,
            'certificates': certificates,
            'revoked': revoked,
            'events': batch.events.select_related('actor')[:15],
            'with_email': len(with_email),
            'pending_email': sum(1 for c in with_email if not c.emailed_at),
            'email_enabled': EmailSettings.load().enabled,
            'has_group': any(c.participant_group for c in everything),
        },
    )


@login_required
def batch_edit(request, pk):
    batch = _get_batch(request, pk)
    if batch is None:
        return HttpResponseForbidden(FORBIDDEN)
    if closed := _closed(request, batch):
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
def batch_cancel(request, pk):
    """Cancela a emissão inteira: revoga todos os certificados. Nada é apagado."""
    batch = _get_batch(request, pk)
    if batch is None:
        return HttpResponseForbidden(FORBIDDEN)
    if closed := _closed(request, batch):
        return closed
    total = active(batch).count()
    form = ReasonForm(request.POST or None)
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
        },
    )


@login_required
def batch_certificate_revoke(request, pk, code):
    batch = _get_batch(request, pk)
    if batch is None:
        return HttpResponseForbidden(FORBIDDEN)
    certificate = get_object_or_404(batch.certificates, code=code)
    if certificate.is_revoked:
        messages.info(request, 'Este certificado já estava revogado.')
        return redirect('batch_detail', pk=batch.pk)
    form = ReasonForm(request.POST or None)
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
        },
    )


@login_required
def batch_zip(request, pk):
    batch = _get_batch(request, pk)
    if batch is None:
        return HttpResponseForbidden(FORBIDDEN)
    return FileResponse(
        build_zip(batch, request=request), as_attachment=True, filename=_filename(batch, 'certificados.zip')
    )


@login_required
def batch_csv(request, pk):
    batch = _get_batch(request, pk)
    if batch is None:
        return HttpResponseForbidden(FORBIDDEN)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{_filename(batch, "certificados")}.csv"'
    response.write('﻿')  # BOM: o Excel abre com acentos corretos
    writer = csv.writer(response, delimiter=';')
    writer.writerow(
        [
            'Nome',
            'Matrícula',
            'Turma',
            'E-mail',
            'Código',
            'Link de validação',
            'Data do certificado',
            'Gerado no sistema em',
            'Enviado por e-mail em',
            'Situação',
            'Revogado em',
            'Motivo da revogação',
        ]
    )
    for c in batch.certificates.order_by('participant_name'):
        writer.writerow([
            c.participant_name,
            c.participant_document,
            c.participant_group,
            c.participant_email,
            c.code,
            request.build_absolute_uri(reverse('certificate_verify_code', args=[c.code])),
            c.certificate_date.strftime('%d/%m/%Y'),
            timezone.localtime(c.issued_at).strftime('%d/%m/%Y %H:%M'),
            timezone.localtime(c.emailed_at).strftime('%d/%m/%Y %H:%M') if c.emailed_at else '',
            'Revogado' if c.is_revoked else 'Válido',
            timezone.localtime(c.revoked_at).strftime('%d/%m/%Y %H:%M') if c.revoked_at else '',
            c.revoked_reason,
        ])  # fmt: skip
    return response


@login_required
@require_POST
def batch_email(request, pk):
    batch = _get_batch(request, pk)
    if batch is None:
        return HttpResponseForbidden(FORBIDDEN)
    if closed := _closed(request, batch):
        return closed
    if not EmailSettings.load().enabled:
        messages.error(request, 'O envio de e-mails está desligado. Configure em Configurações > E-mail.')
        return redirect('batch_detail', pk=batch.pk)
    sent, failed = send_emails(batch, resend=request.POST.get('resend') == '1', request=request)
    if sent:
        messages.success(
            request, f'{sent} certificado{"s" if sent != 1 else ""} enviado{"s" if sent != 1 else ""} por e-mail.'
        )
    if failed:
        messages.error(
            request,
            f'{failed} envio{"s" if failed != 1 else ""} falhou. Confira a configuração de e-mail e tente de novo: '
            'só quem ainda não recebeu será reenviado.',
        )
    if not sent and not failed:
        messages.info(request, 'Não havia certificados pendentes de envio.')
    return redirect('batch_detail', pk=batch.pk)


@login_required
def batch_template(request):
    """Planilha modelo, já com o cabeçalho que a importação reconhece."""
    if not can_manage_batch(request.user):
        return HttpResponseForbidden(FORBIDDEN)
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Participantes'
    sheet.append(['Nome', 'Matrícula', 'Turma', 'E-mail'])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for column, width in zip('ABCD', (40, 16, 18, 36), strict=True):
        sheet.column_dimensions[column].width = width
    for row in range(2, 501):  # matrícula como texto: não perde zeros à esquerda
        sheet.cell(row=row, column=2).number_format = '@'
    buffer = io.BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="modelo-participantes.xlsx"'
    return response
