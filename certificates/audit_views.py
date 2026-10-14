"""Tela de auditoria das emissões de certificados (só administradores)."""

from datetime import datetime, time

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from core.csvsafe import excel_writer
from core.pagination import paginate
from core.permissions import is_platform_admin

from .models import CertificateBatch, CertificateEvent

FORBIDDEN = 'Acesso restrito a administradores.'


class AuditFilterForm(forms.Form):
    q = forms.CharField(
        label='Busca', required=False, widget=forms.TextInput(attrs={'placeholder': 'Código, nome, RA ou evento'})
    )
    acao = forms.ChoiceField(label='Ação', required=False, choices=[('', 'Todas'), *CertificateEvent.Action.choices])
    de = forms.DateField(label='De', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    ate = forms.DateField(label='Até', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    usuario = forms.CharField(label='Usuário', required=False)
    lote = forms.IntegerField(required=False, widget=forms.HiddenInput)


def filtered_events(params):
    form = AuditFilterForm(params)
    qs = CertificateEvent.objects.select_related('batch')
    if not form.is_valid():
        return form, qs.none()
    data = form.cleaned_data
    if data['q']:
        term = data['q'].strip()
        code = term.upper().replace(' ', '')
        qs = qs.filter(
            Q(code__icontains=code)
            | Q(participant_name__icontains=term)
            | Q(participant_document__icontains=term)
            | Q(activity_title__icontains=term)
        )
    if data['acao']:
        qs = qs.filter(action=data['acao'])
    if data['de']:
        qs = qs.filter(created_at__gte=timezone.make_aware(datetime.combine(data['de'], time.min)))
    if data['ate']:
        qs = qs.filter(created_at__lte=timezone.make_aware(datetime.combine(data['ate'], time.max)))
    if data['usuario']:
        qs = qs.filter(actor_name__icontains=data['usuario'].strip())
    if data['lote']:
        qs = qs.filter(batch_id=data['lote'])
    return form, qs


@login_required
def audit_list(request):
    if not is_platform_admin(request.user):
        raise PermissionDenied(FORBIDDEN)
    form, qs = filtered_events(request.GET)
    page, querystring = paginate(request, qs, 50)
    batch = None
    if form.is_valid() and form.cleaned_data['lote']:
        batch = CertificateBatch.objects.filter(pk=form.cleaned_data['lote']).first()
    return render(
        request,
        'certificates/audit.html',
        {
            'form': form,
            'page': page,
            'total': page.paginator.count,
            'querystring': querystring,
            'batch': batch,
            'active': 'audit',
        },
    )


@login_required
def audit_csv(request):
    if not is_platform_admin(request.user):
        raise PermissionDenied(FORBIDDEN)
    _, qs = filtered_events(request.GET)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    stamp = timezone.localtime().strftime('%Y%m%d-%H%M')
    response['Content-Disposition'] = f'attachment; filename="auditoria-certificados-{stamp}.csv"'
    response.write('﻿')
    writer = excel_writer(response)
    writer.writerow(['Data e hora', 'Ação', 'Código', 'Participante', 'Matrícula', 'Atividade/evento', 'Origem',
                     'Usuário', 'IP', 'Detalhes'])  # fmt: skip
    for e in qs.iterator(chunk_size=2000):
        writer.writerow([
            timezone.localtime(e.created_at).strftime('%d/%m/%Y %H:%M:%S'),
            e.get_action_display(),
            e.code,
            e.participant_name,
            e.participant_document,
            e.activity_title,
            'Emissão avulsa' if e.batch_id else 'Atividade',
            e.actor_name or 'Sistema',
            e.ip or '',
            e.summary,
        ])  # fmt: skip
    return response
