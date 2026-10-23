"""Crachás em PDF com o QR Code da inscrição."""

import io
from collections import OrderedDict

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from core.models import SiteSettings
from registrations.qr import QR_PREFIX

from .common import CONFIRMED, managed_activity

# Medidas em mm: (papel, colunas, linhas, largura, altura, margem esquerda, margem superior, espaço entre colunas,
# espaço entre linhas, linha de corte)
BADGE_LAYOUTS = OrderedDict(
    [
        ('a4-8', ('Cartões 90 x 60 mm, 8 por folha A4 (para cortar)', A4, 2, 4, 90, 60, 15, 28.5, 0, 0, True)),
        ('a4-10', ('Etiquetas 99 x 57 mm, 10 por folha A4', A4, 2, 5, 99, 57, 6, 6, 0, 0, False)),
        ('carta-10', ('Etiquetas 101,6 x 50,8 mm, 10 por folha Carta (tipo Pimaco 6183)', LETTER, 2, 5, 101.6,
                      50.8, 4.0, 12.7, 4.7, 0, False)),
    ]
)  # fmt: skip


def _fit(text, font, size, max_width, min_size=8):
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 0.5
    return size


def badges_pdf(activity, registrations, layout_key, offset_x=0.0, offset_y=0.0):
    from certificates.pdf import register_fonts

    register_fonts()
    _, page, cols, rows, w, h, left, top, gap_x, gap_y, cut_lines = BADGE_LAYOUTS[layout_key]
    w, h, left, top, gap_x, gap_y = (v * mm for v in (w, h, left, top, gap_x, gap_y))
    offset_x, offset_y = offset_x * mm, offset_y * mm
    site = SiteSettings.load()
    brand = colors.HexColor(site.safe_primary_color)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=page)
    pdf.setTitle(f'Crachás - {activity.title}')
    page_w, page_h = page
    per_page = cols * rows
    for index, registration in enumerate(registrations):
        if index and index % per_page == 0:
            pdf.showPage()
        if registration is None:  # etiqueta já usada na folha: pula
            continue
        slot = index % per_page
        col, row = slot % cols, slot // cols
        x = left + col * (w + gap_x) + offset_x
        y = page_h - top - (row + 1) * h - row * gap_y - offset_y
        if cut_lines:
            pdf.setStrokeColor(colors.HexColor('#c8ced6'))
            pdf.setDash(2, 2)
            pdf.rect(x, y, w, h, stroke=1, fill=0)
            pdf.setDash()
        pad = 4 * mm
        qr_size = min(h - 2 * pad, 30 * mm)
        text_w = w - 3 * pad - qr_size
        # faixa com a cor da instituição
        pdf.setFillColor(brand)
        pdf.rect(x, y + h - 2.2 * mm, w, 2.2 * mm, stroke=0, fill=1)
        # nome (2 linhas no máximo, fonte ajustada à largura)
        user = registration.user
        name = (user.get_full_name() or user.username).strip()
        parts = name.split()
        first, rest = (parts[0], ' '.join(parts[1:])) if len(parts) > 1 else (name, '')
        pdf.setFillColor(colors.HexColor('#1c2533'))
        size = _fit(first, 'Carlito-Bold', 20, text_w)
        pdf.setFont('Carlito-Bold', size)
        top_line = y + h - pad - 2.2 * mm - size * 0.8
        pdf.drawString(x + pad, top_line, first)
        line_y = top_line
        if rest:
            size2 = _fit(rest, 'Carlito', 12, text_w, 7)
            pdf.setFont('Carlito', size2)
            line_y -= size2 + 2
            pdf.drawString(x + pad, line_y, rest)
        # categoria e atividade na parte de baixo
        pdf.setFillColor(colors.HexColor('#5a6577'))
        bottom = y + pad
        if registration.category_id:
            pdf.setFillColor(brand)
            label = registration.category.name.upper()
            pdf.setFont('Carlito-Bold', _fit(label, 'Carlito-Bold', 9, text_w, 6))
            pdf.drawString(x + pad, bottom + 11, label)
            pdf.setFillColor(colors.HexColor('#5a6577'))
        title = activity.title
        pdf.setFont('Carlito', _fit(title, 'Carlito', 8.5, text_w, 6))
        pdf.drawString(x + pad, bottom, title)
        # QR Code da inscrição (o mesmo do ingresso: serve para o check-in)
        widget = QrCodeWidget(f'{QR_PREFIX}{registration.qr_token}')
        bx0, by0, bx1, by1 = widget.getBounds()
        drawing = Drawing(qr_size, qr_size, transform=[qr_size / (bx1 - bx0), 0, 0, qr_size / (by1 - by0), 0, 0])
        drawing.add(widget)
        renderPDF.draw(drawing, pdf, x + w - pad - qr_size, y + (h - qr_size) / 2 - 1 * mm)
    if not any(registrations):
        pdf.setFont('Helvetica', 12)
        pdf.drawString(20 * mm, page_h - 30 * mm, 'Nenhum inscrito confirmado para gerar crachás.')
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@login_required
def badges(request, pk):
    activity, allowed = managed_activity(request, pk)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    categories = list(activity.categories.all())
    if 'layout' not in request.GET:
        return render(
            request,
            'organizer/badges.html',
            {'activity': activity, 'layouts': [(k, v[0]) for k, v in BADGE_LAYOUTS.items()],
             'categories': categories, 'total': activity.registrations.filter(status=CONFIRMED).count()},
        )  # fmt: skip
    layout = request.GET.get('layout')
    if layout not in BADGE_LAYOUTS:
        layout = 'a4-8'

    def number(name):
        try:
            return max(-15.0, min(15.0, float(request.GET.get(name, '0').replace(',', '.'))))
        except ValueError:
            return 0.0

    qs = (
        activity.registrations.filter(status=CONFIRMED)
        .select_related('user', 'category')
        .order_by('user__first_name', 'user__last_name', 'user__username')
    )
    category = request.GET.get('categoria')
    if category and category.isdigit():
        qs = qs.filter(category_id=int(category))
    blank = request.GET.get('pular', '0')
    skip = int(blank) if blank.isdigit() else 0
    registrations = [None] * min(skip, 40) + list(qs)
    pdf = badges_pdf(activity, registrations, layout, number('dx'), number('dy'))
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{activity.slug}-crachas.pdf"'
    return response
