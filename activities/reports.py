"""Relatórios para a organização/secretaria: planilha de inscritos e listas em PDF."""

import csv
import io

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from attendance.summary import participation
from certificates.models import Certificate
from core.models import SiteSettings
from registrations.models import Registration, RegistrationAnswer

from .models import Activity, Session
from .views import can_manage

BLUE = colors.HexColor('#0b4f8a')
LIGHT = colors.HexColor('#eef3f9')


def _fmt(dt, pattern='%d/%m/%Y %H:%M'):
    return timezone.localtime(dt).strftime(pattern) if dt else ''


def _filename(activity, suffix):
    return f'{slugify(activity.title)[:50] or "atividade"}-{suffix}'


# ---------- CSV (abre direto no Excel)


@login_required
def registrations_csv(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')

    statuses = [Registration.Status.CONFIRMED, Registration.Status.WAITLISTED, Registration.Status.CANCELLED]
    sessions, rows = participation(activity, statuses=statuses)
    questions = list(activity.questions.all())
    answers = {
        (a.registration_id, a.question_id): a.value
        for a in RegistrationAnswer.objects.filter(registration__activity=activity)
    }
    certificates = dict(
        Certificate.objects.filter(registration__activity=activity).values_list('registration_id', 'code')
    )
    order = {Registration.Status.CONFIRMED: 0, Registration.Status.WAITLISTED: 1, Registration.Status.CANCELLED: 2}
    rows.sort(key=lambda r: (order[r.registration.status], r.registration.waitlisted_at or r.registration.created_at))

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{_filename(activity, "inscritos")}.csv"'
    # BOM + ponto e vírgula: o Excel em português abre com acentos e colunas corretas.
    response.write('﻿')
    writer = csv.writer(response, delimiter=';')
    writer.writerow(
        ['Nome', 'E-mail', 'Matrícula', 'Situação', 'Inscrito em']
        + [q.label for q in questions]
        + [f'{s.title} ({_fmt(s.start_at, "%d/%m")})' for s in sessions]
        + ['Presenças', 'Frequência (%)', 'Certificado']
    )
    for row in rows:
        registration, user = row.registration, row.user
        confirmed = registration.status == Registration.Status.CONFIRMED
        writer.writerow(
            [
                user.get_full_name() or user.username,
                user.email,
                user.institutional_id,
                registration.get_status_display(),
                _fmt(registration.created_at),
            ]
            + [answers.get((registration.id, q.id), '') for q in questions]
            + [('P' if mark else 'F') if confirmed else '' for mark in row.marks]
            + [
                row.count if confirmed else '',
                row.percent if confirmed and sessions else '',
                certificates.get(registration.id, ''),
            ]
        )
    return response


# ---------- PDFs


def _pdf_response(activity, suffix, build):
    buffer = io.BytesIO()
    build(buffer)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{_filename(activity, suffix)}.pdf"'
    return response


def _header(title, subtitle):
    styles = {
        'inst': ParagraphStyle('inst', fontName='Helvetica-Bold', fontSize=10, textColor=BLUE),
        'title': ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=16, leading=20, spaceBefore=4),
        'sub': ParagraphStyle('sub', fontName='Helvetica', fontSize=10, leading=14, textColor=colors.HexColor('#444')),
    }
    return [
        Paragraph(SiteSettings.load().institution_name.upper(), styles['inst']),
        Paragraph(title, styles['title']),
        Paragraph(subtitle, styles['sub']),
        Spacer(1, 0.4 * cm),
    ]


def _table(data, col_widths, align_center_from=None):
    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 9),
        ('FONT', (0, 1), (-1, -1), 'Helvetica', 9),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 0), (-1, 0), BLUE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT]),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#c8d3e0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if align_center_from is not None:
        style.append(('ALIGN', (align_center_from, 0), (-1, -1), 'CENTER'))
    table.setStyle(TableStyle(style))
    return table


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.HexColor('#666'))
    canvas.drawString(doc.leftMargin, 0.8 * cm, f'Gerado em {_fmt(timezone.now())}')
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 0.8 * cm, f'Página {doc.page}')
    canvas.restoreState()


@login_required
def attendance_pdf(request, pk):
    """Frequência consolidada: uma linha por inscrito confirmado, uma coluna por sessão."""
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
    sessions, rows = participation(activity)

    def build(buffer):
        page = landscape(A4)
        doc = SimpleDocTemplate(
            buffer, pagesize=page, leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.2 * cm, bottomMargin=1.4 * cm
        )
        doc.title = f'Frequência - {activity.title}'
        subtitle = (
            f'{activity.get_activity_type_display()} · {_fmt(activity.start_date, "%d/%m/%Y")} a '
            f'{_fmt(activity.end_date, "%d/%m/%Y")} · {len(rows)} inscrito(s) · {len(sessions)} sessão(ões)'
        )
        if activity.certificate_enabled:
            subtitle += f' · frequência mínima para certificado: {activity.min_attendance_percent}%'
        header = (
            ['#', 'Participante', 'Matrícula'] + [_fmt(s.start_at, '%d/%m %H:%M') for s in sessions] + ['Total', '%']
        )
        data = [header]
        for i, row in enumerate(rows, 1):
            data.append(
                [i, row.user.get_full_name() or row.user.username, row.user.institutional_id]
                + ['P' if m else '-' for m in row.marks]
                + [row.count, f'{row.percent}%' if sessions else '-']
            )
        if not rows:
            data.append(['', 'Nenhum inscrito confirmado.', ''] + [''] * len(sessions) + ['', ''])
        usable = page[0] - 2.4 * cm
        fixed = [0.8 * cm, 7.5 * cm, 3 * cm]
        tail = [1.3 * cm, 1.4 * cm]
        session_w = max(0.9 * cm, (usable - sum(fixed) - sum(tail)) / max(len(sessions), 1))
        widths = fixed + [session_w] * len(sessions) + tail
        doc.build(
            _header(f'Frequência: {activity.title}', subtitle) + [_table(data, widths, align_center_from=3)],
            onFirstPage=_footer,
            onLaterPages=_footer,
        )

    return _pdf_response(activity, 'frequencia', build)


@login_required
def signature_pdf(request, pk):
    """Lista de assinatura de uma sessão, para impressão (útil como comprovante físico)."""
    session = get_object_or_404(Session.objects.select_related('activity'), pk=pk)
    activity = session.activity
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
    _, rows = participation(activity)

    def build(buffer):
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm
        )
        doc.title = f'Lista de assinatura - {session.title}'
        subtitle = (
            f'{session.title} · {_fmt(session.start_at)} até {_fmt(session.end_at, "%H:%M")}'
            f' · {session.location or activity.location or "local a definir"}'
        )
        data = [['#', 'Participante', 'Matrícula', 'Assinatura']]
        for i, row in enumerate(rows, 1):
            data.append([i, row.user.get_full_name() or row.user.username, row.user.institutional_id, ''])
        table = _table(data, [0.8 * cm, 7.2 * cm, 3 * cm, 7 * cm])
        table.setStyle(TableStyle([('TOPPADDING', (0, 1), (-1, -1), 9), ('BOTTOMPADDING', (0, 1), (-1, -1), 9)]))
        doc.build(
            _header(f'Lista de assinatura: {activity.title}', subtitle) + [table],
            onFirstPage=_footer,
            onLaterPages=_footer,
        )

    return _pdf_response(activity, f'assinaturas-{slugify(session.title)[:30]}', build)
