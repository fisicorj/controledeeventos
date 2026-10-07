"""Geração do certificado em PDF (A4 paisagem) com ReportLab.

Dois visuais:
- com "Arte de fundo" (Configurações): a arte ocupa a página inteira e o texto é escrito por cima,
  no mesmo arranjo do modelo de declaração da instituição;
- sem arte: moldura padrão do sistema na cor da instituição, com o logo.

O texto usa Carlito (métrica idêntica à Calibri) e o nome usa Liberation Serif negrito itálico
(métrica idêntica à Times New Roman). As duas fontes são livres (SIL OFL) e ficam em certificates/fonts.
"""

import io
from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from core.models import SiteSettings

FONT_DIR = Path(__file__).resolve().parent / 'fonts'
BODY, BODY_BOLD, NAME_FONT = 'Carlito', 'Carlito-Bold', 'CertName'
TEXT_COLOR = colors.HexColor('#3c3c3c')
GOLD = colors.HexColor('#e5a01a')
YELLOW = colors.HexColor('#ffe59a')
MONTHS = [
    'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
    'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
]  # fmt: skip

# Posições (em pontos, medidas a partir do TOPO da página) tiradas do modelo de referência.
Y_OPENING, Y_NAME, Y_BODY, BODY_LEADING = 247.6, 273.8, 297.9, 20.5
GAP_DATE = 37.5
Y_SIGN_LINE, Y_SIGNER, Y_ROLE = 455.0, 469.8, 484.4
SIGN_LINE_HALF = 83.5
BODY_SIZE, NAME_SIZE, SIGNER_SIZE = 14, 24, 12
TEXT_WIDTH = 150 * mm
MAX_DATE_BASELINE = 388  # a linha de local e data nunca desce além disto (fica acima da assinatura)

_fonts_ready = False


def register_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    pdfmetrics.registerFont(TTFont(BODY, FONT_DIR / 'Carlito-Regular.ttf'))
    pdfmetrics.registerFont(TTFont(BODY_BOLD, FONT_DIR / 'Carlito-Bold.ttf'))
    pdfmetrics.registerFont(TTFont(NAME_FONT, FONT_DIR / 'LiberationSerif-BoldItalic.ttf'))
    _fonts_ready = True


# ---------- Textos


def _day(d):
    return '1º' if d.day == 1 else str(d.day)


def long_date(dt):
    d = dt if isinstance(dt, date) and not isinstance(dt, datetime) else timezone.localtime(dt)
    return f'{_day(d)} de {MONTHS[d.month - 1]} de {d.year}'


def period_phrase(start, end):
    """'no dia 29 de agosto de 2026', 'de 10 a 14 de agosto de 2026', 'de 28 de agosto a 2 de setembro de 2026'."""
    s, e = timezone.localtime(start).date(), timezone.localtime(end).date()
    if s == e:
        return f'no dia {_day(s)} de {MONTHS[s.month - 1]} de {s.year}'
    if (s.year, s.month) == (e.year, e.month):
        return f'de {_day(s)} a {_day(e)} de {MONTHS[s.month - 1]} de {s.year}'
    if s.year == e.year:
        return f'de {_day(s)} de {MONTHS[s.month - 1]} a {_day(e)} de {MONTHS[e.month - 1]} de {s.year}'
    return f'de {_day(s)} de {MONTHS[s.month - 1]} de {s.year} a {_day(e)} de {MONTHS[e.month - 1]} de {e.year}'


def certificate_text(certificate, site):
    """Textos do certificado. O corpo é uma lista de linhas; cada linha é uma lista de trechos
    (texto, negrito). Mantém as quebras do modelo: título / local / período e duração."""
    hours = certificate.workload_hours
    tail = period_phrase(certificate.start_date, certificate.end_date)
    if hours:
        tail += f', com duração de {hours} hora{"s" if hours != 1 else ""}'
    tail += '.'
    body = [[('participou do “', False), (certificate.activity_title, True), ('”,', False)]]
    place = site.certificate_place.strip().rstrip(',')
    if place:
        body.append([(f'realizado {place},', False)])
        body.append([(tail, False)])
    else:
        body.append([(f'realizado {tail}', False)])
    city = site.city.strip()
    issued = certificate.certificate_date
    return {
        'title': site.certificate_title.strip(),
        'opening': site.certificate_opening.strip(),
        'name': f'{certificate.participant_name},',
        'body': body,
        'date_line': f'{city}, {long_date(issued)}.' if city else f'{long_date(issued)}.',
    }


def verify_url(certificate):
    return settings.SITE_URL + reverse('certificate_verify_code', args=[certificate.code])


# ---------- Desenho


def _image(field):
    """Lê uma imagem enviada nas configurações; devolve None se não houver ou se estiver ilegível."""
    if not field:
        return None
    try:
        with field.open('rb') as handle:
            return ImageReader(io.BytesIO(handle.read()))
    except Exception:
        return None


def _draw_fit(pdf, image, center_x, bottom_y, max_w, max_h):
    img_w, img_h = image.getSize()
    scale = min(max_w / img_w, max_h / img_h)
    w, h = img_w * scale, img_h * scale
    pdf.drawImage(image, center_x - w / 2, bottom_y, width=w, height=h, mask='auto')


def _rich_lines(runs, max_width, size):
    """Converte trechos em linhas prontas, colando aspas ao título (sem espaço)."""
    # Monta tokens: cada palavra recebe a fonte do trecho; trechos colados (sem espaço na
    # fronteira) viram uma palavra só com fontes misturadas.
    tokens = []  # cada token é lista de (texto, fonte)
    for text, bold in runs:
        font = BODY_BOLD if bold else BODY
        parts = text.split(' ')
        for i, part in enumerate(parts):
            if i == 0 and tokens and not text.startswith(' ') and part:
                tokens[-1].append((part, font))
            elif part:
                tokens.append([(part, font)])
    space = pdfmetrics.stringWidth(' ', BODY, size)
    lines, current, width = [], [], 0.0
    for token in tokens:
        w = sum(pdfmetrics.stringWidth(t, f, size) for t, f in token)
        extra = (space if current else 0) + w
        if current and width + extra > max_width:
            lines.append((current, width))
            current, width, extra = [], 0.0, w
        current.append(token)
        width += extra
    if current:
        lines.append((current, width))
    return lines


def _draw_rich_line(pdf, line, width, center_x, y, size):
    space = pdfmetrics.stringWidth(' ', BODY, size)
    x = center_x - width / 2
    for token in line:
        for text, font in token:
            pdf.setFont(font, size)
            pdf.drawString(x, y, text)
            x += pdfmetrics.stringWidth(text, font, size)
        x += space


def _fit_size(text, font, max_size, min_size, max_width):
    size = max_size
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 0.5
    return size


def _draw_default_frame(pdf, site, width, height, brand):
    pdf.setFillColor(brand)
    pdf.rect(0, 0, width, height, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.rect(1 * cm, 1 * cm, width - 2 * cm, height - 2 * cm, stroke=0, fill=1)
    pdf.setFillColor(YELLOW)
    pdf.rect(1 * cm, height - 2.2 * cm, width - 2 * cm, 1.2 * cm, stroke=0, fill=1)
    pdf.setFillColor(brand)
    pdf.setFont(BODY_BOLD, 14)
    pdf.drawCentredString(width / 2, height - 1.75 * cm, site.institution_name.upper())
    logo = _image(site.logo)
    if logo:
        _draw_fit(pdf, logo, width / 2, height - 4.3 * cm, 8 * cm, 1.8 * cm)
    return 2.4 * cm if logo else 1.0 * cm  # topo do título, abaixo da faixa e do logo


def certificate_pdf(certificate, signer_name=''):
    """Gera o PDF. signer_name é o organizador da atividade, usado quando a plataforma
    não define quem assina os certificados."""
    register_fonts()
    site = SiteSettings.load()
    texts = certificate_text(certificate, site)
    width, height = landscape(A4)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f'Certificado {certificate.code}')
    pdf.setAuthor(site.institution_name)

    def top(y_from_top):
        return height - y_from_top

    background = _image(site.certificate_background)
    brand = colors.HexColor(site.safe_primary_color)
    if background:
        pdf.drawImage(background, 0, 0, width=width, height=height)
        center_x = width / 2 + site.certificate_offset_mm * mm
        title_color, title_y = GOLD, 82
        lift = 0
    else:
        title_offset = _draw_default_frame(pdf, site, width, height, brand)
        center_x = width / 2 + site.certificate_offset_mm * mm
        title_color, title_y = brand, 3.2 * cm + title_offset
        lift = 30 if title_offset > 1 * cm else 45  # sem a faixa da arte, o texto sobe um pouco

    # Título (em branco quando a arte de fundo já traz o título)
    if texts['title']:
        title = texts['title'].upper()
        pdf.setFillColor(title_color)
        size = _fit_size(title, 'Times-Bold', 40, 20, width - 6 * cm)
        pdf.setFont('Times-Bold', size)
        spacing = size * 0.18
        title_width = pdfmetrics.stringWidth(title, 'Times-Bold', size) + spacing * (len(title) - 1)
        pdf.saveState()  # o espaçamento entre letras vale só para o título
        text_obj = pdf.beginText(center_x - title_width / 2, top(title_y))
        text_obj.setFont('Times-Bold', size)
        text_obj.setCharSpace(spacing)
        text_obj.textOut(title)
        pdf.drawText(text_obj)
        pdf.restoreState()

    # Matrícula/RA no canto superior direito
    document = (certificate.participant_document or '').strip()
    if document:
        pdf.setFont(BODY, 10)
        doc_y = 38.6 if background else 2.9 * cm
        # Sobre a arte (topo escuro) o texto fica branco; na moldura padrão, cinza.
        pdf.setFillColor(colors.white if background else TEXT_COLOR)
        label = f'{site.document_label}: {document}' if site.document_label else document
        pdf.drawRightString(width - (32 if background else 1.6 * cm), top(doc_y), label)

    pdf.setFillColor(TEXT_COLOR)
    # Abertura
    if texts['opening']:
        pdf.setFont(BODY, BODY_SIZE)
        pdf.drawCentredString(center_x, top(Y_OPENING - lift), texts['opening'])
    # Nome
    name_size = _fit_size(texts['name'], NAME_FONT, NAME_SIZE, 14, TEXT_WIDTH + 30 * mm)
    pdf.setFont(NAME_FONT, name_size)
    pdf.drawCentredString(center_x, top(Y_NAME - lift), texts['name'])
    # Corpo. Títulos longos quebram em mais linhas; se o texto não couber acima da assinatura,
    # o espaçamento e depois a fonte diminuem um pouco até caber.
    for size in (BODY_SIZE, 13, 12, 11, 10):
        scale = size / BODY_SIZE
        lines = [line for runs in texts['body'] for line in _rich_lines(runs, TEXT_WIDTH, size)]
        leading, gap = BODY_LEADING * scale, GAP_DATE * scale
        if Y_BODY - lift + (len(lines) - 1) * leading + gap <= MAX_DATE_BASELINE:
            break
    y = Y_BODY - lift
    for line, line_width in lines:
        _draw_rich_line(pdf, line, line_width, center_x, top(y), size)
        y += leading
    # Local e data
    pdf.setFont(BODY, size)
    pdf.drawCentredString(center_x, top(y - leading + gap), texts['date_line'])

    # Assinatura: a definida na plataforma tem prioridade sobre o organizador da atividade
    use_site_signer = bool(site.signer_name)
    name = site.signer_name if use_site_signer else (signer_name or 'Organização')
    role = site.signer_role or f'Organização, {site.institution_name}'
    signature = _image(site.signature_image) if use_site_signer else None
    if signature:
        # A assinatura cruza a linha, como numa assinatura feita à mão.
        _draw_fit(pdf, signature, center_x, top(Y_SIGN_LINE + 59), 110, 115)
    pdf.setStrokeColor(TEXT_COLOR)
    pdf.setLineWidth(0.6)
    pdf.line(center_x - SIGN_LINE_HALF, top(Y_SIGN_LINE), center_x + SIGN_LINE_HALF, top(Y_SIGN_LINE))
    pdf.setFillColor(TEXT_COLOR)
    pdf.setFont(BODY, SIGNER_SIZE)
    pdf.drawCentredString(center_x, top(Y_SIGNER), name)
    pdf.drawCentredString(center_x, top(Y_ROLE), role)

    # Validação: só o QR Code no canto inferior direito (fora da área dos logos).
    # Ele abre a página pública de validação deste certificado.
    if site.certificate_show_qr:
        qr = QrCodeWidget(verify_url(certificate))
        x0, y0, x1, y1 = qr.getBounds()
        size_qr = 2.0 * cm
        drawing = Drawing(size_qr, size_qr, transform=[size_qr / (x1 - x0), 0, 0, size_qr / (y1 - y0), 0, 0])
        drawing.add(qr)
        renderPDF.draw(drawing, pdf, width - 1.3 * cm - size_qr, 1.15 * cm)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
