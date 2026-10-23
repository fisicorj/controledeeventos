"""Importação da planilha: nova emissão, planilha completa, inclusão de participantes e prévia."""

import json
import re
import secrets
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render

from core.permissions import can_manage_batch

from ..batch import (
    existing_keys,
    issue_batch,
)
from ..batch_import import EVENT_COLUMNS, SpreadsheetError, parse_participants, read_event, read_table
from ..forms import BatchForm, FullSpreadsheetForm, SpreadsheetForm
from ..models import CertificateBatch
from .common import FORBIDDEN, SESSION_KEY, closed_redirect, get_managed_batch


def _read_spreadsheet(form, batch=None):
    """Lê e valida a planilha. Devolve as linhas ou None (com o erro anotado no formulário)."""
    try:
        table = read_table(form.cleaned_data['spreadsheet'])
        rows, columns = parse_participants(table, fix_case=form.cleaned_data['fix_case'], existing=existing_keys(batch))
    except SpreadsheetError as exc:
        form.add_error('spreadsheet', str(exc))
        return None, None
    return rows, columns


# As linhas da prévia (até 3.000) ficam num arquivo temporário, não na sessão: a sessão guarda só o essencial.
PREVIEW_MAX_AGE = 24 * 3600


def _preview_dir():
    return Path(settings.PRIVATE_MEDIA_ROOT) / 'tmp' / 'importacoes'


def _preview_file(token):
    return _preview_dir() / f'{token}.json'


def _clean_old_previews():
    limit = time.time() - PREVIEW_MAX_AGE
    for path in _preview_dir().glob('*.json'):
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
        except OSError:
            pass


def _store_preview(request, rows, columns, filename, batch=None, event=None, mode='form'):
    _drop_preview(request)
    token = secrets.token_urlsafe(16)
    _preview_dir().mkdir(parents=True, exist_ok=True)
    _clean_old_previews()
    _preview_file(token).write_text(json.dumps([row.to_dict() for row in rows], ensure_ascii=False), 'utf-8')
    request.session[SESSION_KEY] = {
        'token': token,
        'batch_id': batch.pk if batch else None,
        'event': event,
        'filename': filename,
        'columns': sorted(columns),
        'mode': mode,  # "form": dados do evento digitados; "full": vieram da planilha completa
    }
    return redirect('batch_preview')


def _load_preview(request):
    """Dados da prévia com as linhas lidas do arquivo; None se não houver ou se o arquivo sumiu."""
    data = request.session.get(SESSION_KEY)
    if not data or not re.fullmatch(r'[\w-]+', data.get('token', '')):
        return None
    try:
        rows = json.loads(_preview_file(data['token']).read_text('utf-8'))
    except (OSError, ValueError):
        request.session.pop(SESSION_KEY, None)
        return None
    return {**data, 'rows': rows}


def _drop_preview(request):
    data = request.session.pop(SESSION_KEY, None)
    if data and re.fullmatch(r'[\w-]+', data.get('token', '')):
        _preview_file(data['token']).unlink(missing_ok=True)


@login_required
def batch_new(request):
    if not can_manage_batch(request.user):
        raise PermissionDenied(FORBIDDEN)
    # Voltando da prévia ("Enviar outra planilha"), o formulário vem preenchido com o evento digitado.
    previous = request.session.get(SESSION_KEY) or {}
    initial = previous.get('event') if not previous.get('batch_id') and previous.get('mode') != 'full' else None
    event_form = BatchForm(request.POST or None, initial=initial)
    sheet_form = SpreadsheetForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and event_form.is_valid() and sheet_form.is_valid():
        rows, columns = _read_spreadsheet(sheet_form)
        if rows is not None:
            event = _event_session(event_form.cleaned_data)
            return _store_preview(request, rows, columns, sheet_form.cleaned_data['spreadsheet'].name, event=event)
    return render(
        request, 'certificates/batch_form.html', {'event_form': event_form, 'sheet_form': sheet_form, 'batch': None}
    )


def _event_session(cleaned):
    return {key: value.isoformat() if isinstance(value, date) else value for key, value in cleaned.items()}


@login_required
def batch_full(request):
    """Nova emissão com a planilha completa: evento e participantes no mesmo arquivo (um evento por planilha)."""
    if not can_manage_batch(request.user):
        raise PermissionDenied(FORBIDDEN)
    sheet_form = FullSpreadsheetForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and sheet_form.is_valid():
        upload = sheet_form.cleaned_data['spreadsheet']
        try:
            table = read_table(upload)
            event = read_event(table)
            # Os dados lidos passam pelas mesmas validações do formulário digitado (datas, carga horária...).
            event_form = BatchForm(
                data={key: '' if value is None else value for key, value in _event_session(event).items()}
            )
            if not event_form.is_valid():
                labels = {**EVENT_COLUMNS, 'start_date': 'Início', 'end_date': 'Término'}
                for field, errors in event_form.errors.items():
                    for error in errors:
                        prefix = f'Coluna {labels[field]}: ' if field in labels else ''
                        sheet_form.add_error('spreadsheet', prefix + error)
                raise SpreadsheetError('')
            rows, columns = parse_participants(table, fix_case=sheet_form.cleaned_data['fix_case'])
        except SpreadsheetError as exc:
            if str(exc):
                sheet_form.add_error('spreadsheet', str(exc))
        else:
            return _store_preview(
                request, rows, columns, upload.name, event=_event_session(event_form.cleaned_data), mode='full'
            )
    return render(
        request,
        'certificates/batch_full.html',
        {'sheet_form': sheet_form, 'columns': EVENT_COLUMNS},
    )


@login_required
def batch_add(request, pk):
    batch = get_managed_batch(request, pk)
    if batch is None:
        raise PermissionDenied(FORBIDDEN)
    if closed := closed_redirect(request, batch):
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
        raise PermissionDenied(FORBIDDEN)
    data = _load_preview(request)
    if not data:
        messages.info(request, 'Envie a planilha para ver a prévia.')
        return redirect('batch_list')
    batch = None
    if data['batch_id']:
        batch = get_managed_batch(request, data['batch_id'])
        if batch is None:
            raise PermissionDenied(FORBIDDEN)
        if closed := closed_redirect(request, batch):
            _drop_preview(request)
            return closed

    rows = data['rows']
    valid = [r for r in rows if not r['errors']]

    if request.method == 'POST':
        return _issue_preview(request, data, batch, rows, valid)
    return render(request, 'certificates/batch_preview.html', _preview_context(data, batch, rows, valid))


def _issue_preview(request, data, batch, rows, valid):
    """Confirmação da prévia: cria o lote (se for novo) e emite as linhas válidas."""
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
    _drop_preview(request)
    skipped = len(rows) - len(created)
    text = f'{len(created)} certificado{"s" if len(created) != 1 else ""} emitido{"s" if len(created) != 1 else ""}.'
    if skipped:
        plural = skipped != 1
        text += f' {skipped} linha{"s" if plural else ""} com problema {"ficaram" if plural else "ficou"} de fora.'
    messages.success(request, text)
    return redirect('batch_detail', pk=batch.pk)


def _preview_context(data, batch, rows, valid):
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
    return context
