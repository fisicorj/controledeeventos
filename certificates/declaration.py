"""Declaração de horas em PDF (A4 retrato): resumo dos certificados válidos da pessoa, para entregar à coordenação.

A declaração não substitui os certificados: cada linha traz o código, e qualquer um confere a autenticidade na
página de validação. Por isso o rodapé explica como conferir."""

import io
from xml.sax.saxutils import escape

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.models import SiteSettings

from .pdf import BODY, BODY_BOLD, LOGO_PX, _image, long_date, register_fonts

GRID = colors.HexColor('#d0d5dd')
MUTED = colors.HexColor('#5c6470')


def _styles(brand):
    base = ParagraphStyle('base', fontName=BODY, fontSize=10.5, leading=14, textColor=colors.HexColor('#24292f'))
    return {
        'base': base,
        'title': ParagraphStyle(
            'title',
            parent=base,
            fontName=BODY_BOLD,
            fontSize=18,
            leading=22,
            textColor=brand,
            alignment=TA_CENTER,
            spaceAfter=4 * mm,
        ),  # fmt: skip
        'center': ParagraphStyle('center', parent=base, alignment=TA_CENTER, textColor=MUTED),
        'semester': ParagraphStyle(
            'semester',
            parent=base,
            fontName=BODY_BOLD,
            fontSize=12,
            leading=16,
            textColor=brand,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        ),  # fmt: skip
        'cell': ParagraphStyle('cell', parent=base, fontSize=9.5, leading=12),
        'small': ParagraphStyle('small', parent=base, fontSize=8.5, leading=11, textColor=MUTED),
    }


def _brand(site):
    try:
        return colors.HexColor(site.primary_color or '#1f4e79')
    except ValueError:
        return colors.HexColor('#1f4e79')


def _semester_table(items, hours, st, brand):
    rows = [['Atividade', 'Tipo', 'Data', 'Horas', 'Código']]
    for c in items:
        rows.append([
            Paragraph(escape(c.activity_title), st['cell']),
            Paragraph(escape(c.activity_type), st['cell']),
            c.certificate_date.strftime('%d/%m/%Y'),
            f'{c.workload_hours} h' if c.workload_hours else '-',
            c.code,
        ])  # fmt: skip
    rows.append(['', '', 'Subtotal', f'{hours} h', ''])
    table = Table(rows, colWidths=[72 * mm, 30 * mm, 22 * mm, 16 * mm, 30 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, -1), BODY, 9.5),
        ('FONT', (0, 0), (-1, 0), BODY_BOLD, 9.5),
        ('FONT', (0, -1), (-1, -1), BODY_BOLD, 9.5),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 0), (-1, 0), brand),
        ('LINEBELOW', (0, 1), (-1, -2), 0.4, GRID),
        ('LINEABOVE', (0, -1), (-1, -1), 0.8, brand),
        ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))  # fmt: skip
    return table


def declaration_pdf(user, semesters):
    """semesters: saída de history.by_semester (já filtrada, se for o caso). Devolve os bytes do PDF."""
    register_fonts()
    # <b> nos parágrafos usa o Carlito negrito
    pdfmetrics.registerFontFamily(BODY, normal=BODY, bold=BODY_BOLD, italic=BODY, boldItalic=BODY_BOLD)
    site = SiteSettings.load()
    brand = _brand(site)
    st = _styles(brand)
    name = user.get_full_name() or user.username
    total = sum(hours for _, _, hours in semesters)
    count = sum(len(items) for _, items, _ in semesters)
    story = []
    logo = _image(site.logo, LOGO_PX)
    if logo:
        w, h = logo.getSize()
        scale = min(50 * mm / w, 18 * mm / h)
        story += [Image(logo, width=w * scale, height=h * scale), Spacer(1, 3 * mm)]
    institution = site.institution_name.strip() or site.portal_name
    story += [
        Paragraph(escape(institution), st['center']),
        Spacer(1, 4 * mm),
        Paragraph('Declaração de horas certificadas', st['title']),
    ]
    who = f'<b>{escape(name)}</b>'
    if user.institutional_id:
        who += f' (matrícula ou CPF {escape(user.institutional_id)})'
    labels = [label for label, _, _ in semesters]
    period = (
        f'no semestre {labels[0]}'
        if len(labels) == 1
        else (f'entre os semestres {labels[-1]} e {labels[0]}' if labels else '')
    )
    story.append(Paragraph(
        f'Declaramos que {who} possui {count} certificado{"s" if count != 1 else ""} válido{"s" if count != 1 else ""} '
        f'registrado{"s" if count != 1 else ""} no portal {escape(site.portal_name)} {period}, '
        f'somando <b>{total} hora{"s" if total != 1 else ""}</b>, conforme a relação abaixo.',
        st['base'],
    ))  # fmt: skip
    for label, items, hours in semesters:
        story.append(
            KeepTogether([Paragraph(f'Semestre {label}', st['semester']), _semester_table(items, hours, st, brand)])
        )
    verify = settings.SITE_URL + reverse('certificate_verify')
    city = site.city.strip()
    today = long_date(timezone.localdate())
    story += [
        Spacer(1, 6 * mm),
        Paragraph(f'{escape(city)}, {today}.' if city else f'{today}.', st['base']),
        Spacer(1, 6 * mm),
        Paragraph(
            'Certificados sem carga horária aparecem com "-" e não entram na soma. O semestre é o da data do '
            'certificado. Este documento resume os certificados e não os substitui: a autenticidade de cada um '
            f'pode ser conferida em {escape(verify)} com o código indicado.',
            st['small'],
        ),
    ]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(BODY, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, 12 * mm, f'{site.portal_name} · emitida em {timezone.localtime():%d/%m/%Y %H:%M}')
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f'Página {doc.page}')
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=20 * mm,
        title=f'Declaração de horas - {name}', author=site.portal_name,
    )  # fmt: skip
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
