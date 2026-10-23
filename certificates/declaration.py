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
            Paragraph(escape(c.full_title) + (' *' if c.claimed_by_id else ''), st['cell']),
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


DAILY_LIMIT = 20  # declarações novas por conta, por dia (SEC-28 da 4ª auditoria)


class TooMany(Exception):
    """A conta já emitiu DAILY_LIMIT declarações diferentes hoje."""


def record(user, semesters, semester=''):
    """Grava a declaração (o que vai impresso) e devolve o registro, com o código de conferência.

    Baixar de novo no mesmo dia, com a mesma lista, devolve a mesma declaração (mesmo código), em vez de criar
    outra: cada declaração é uma página pública, e não faz sentido haver várias iguais (SEC-28)."""
    from .models import Certificate, HoursDeclaration

    items = [
        {
            'codigo': c.code, 'atividade': c.full_title, 'tipo': c.activity_type,
            'data': c.certificate_date.isoformat(), 'horas': c.workload_hours or 0, 'vinculado': bool(c.claimed_by_id),
        }
        for _, certificates, _ in semesters
        for c in certificates
    ]  # fmt: skip
    holder = user.get_full_name() or user.username
    today = HoursDeclaration.objects.filter(user=user, cancelled_at__isnull=True, created_at__date=timezone.localdate())
    for previous in today.filter(semester=semester, holder_name=holder):
        if previous.items == items:
            return previous
    if today.count() >= DAILY_LIMIT:
        raise TooMany
    declaration = HoursDeclaration(
        user=user, holder_name=holder, semester=semester, items=items,
        total_hours=sum(hours for _, _, hours in semesters),
    )  # fmt: skip
    while Certificate.objects.filter(code=declaration.code).exists():  # um código só, entre os dois tipos
        declaration.code = HoursDeclaration._meta.get_field('code').default()
    declaration.save()
    return declaration


def declaration_url(declaration):
    return settings.SITE_URL + reverse('declaration_verify', args=[declaration.code])


def _qr(url, size):
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    widget = QrCodeWidget(url)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    return drawing


def declaration_pdf(user, semesters, declaration=None):
    """semesters: saída de history.by_semester (já filtrada, se for o caso). Devolve os bytes do PDF.
    Com `declaration` (record), o PDF leva o código e o QR da página pública de conferência."""
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
    if user.institutional_id and getattr(user, 'student_id_confirmed', False):
        # Só a matrícula confirmada pelo e-mail institucional: um número só digitado não vai num documento
        # que diz "Declaramos" em nome da instituição.
        who += f' (matrícula {escape(user.institutional_id)})'
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
    if declaration is not None:
        url = declaration_url(declaration)
        check = Paragraph(
            f'<b>Confira esta declaração</b> pelo QR ao lado ou em {escape(url)}. A página mostra a lista oficial '
            f'emitida pelo portal (código {declaration.code}) e se cada certificado continua válido; qualquer '
            'diferença com este documento indica alteração.',
            st['small'],
        )
        box = Table([[_qr(url, 24 * mm), check]], colWidths=[28 * mm, 142 * mm])
        box.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (0, 0), 0)]))
        story += [Spacer(1, 4 * mm), box]
    if any(c.claimed_by_id for _, items, _ in semesters for c in items):
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            '* Certificado de emissão avulsa enviado a outro e-mail e vinculado a esta conta pela própria pessoa, '
            'com o código do certificado.',
            st['small'],
        ))  # fmt: skip

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
