"""Ferramentas de operação do organizador: duplicar edição, materiais pós-evento, crachás e números."""

import io
import mimetypes
from collections import Counter, OrderedDict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from attendance.models import Attendance
from core.models import SiteSettings
from core.permissions import can_manage
from registrations.models import Registration, RegistrationAnswer, SurveyResponse
from registrations.qr import QR_PREFIX

from .forms import DuplicateForm, MaterialForm
from .models import Activity, Material, RegistrationQuestion

CONFIRMED = Registration.Status.CONFIRMED


def _managed(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    return activity, can_manage(request.user, activity)


# ---------- Duplicar para a próxima edição


@transaction.atomic
def duplicate_activity(activity, title, start_date):
    """Copia a atividade como rascunho, deslocando todas as datas pelo mesmo intervalo.
    Vão junto: sessões (e quem fala em cada uma), perguntas, palestrantes, tipos de vaga,
    configuração de certificado e pesquisa. Não vão: inscrições, comunicados e materiais."""
    delta = start_date - activity.start_date
    original_pk = activity.pk
    sessions = list(activity.sessions.prefetch_related('speakers'))
    speakers = list(activity.speakers.all())
    questions = list(activity.questions.all())
    categories = list(activity.categories.all())

    copy = Activity.objects.get(pk=original_pk)
    copy.pk = None
    copy.slug = ''
    copy.title = title
    copy.status = Activity.Status.DRAFT
    copy.previous_edition_id = original_pk
    for field in ('start_date', 'end_date', 'registration_start', 'registration_end'):
        setattr(copy, field, getattr(activity, field) + delta)
    copy.save()

    speaker_map = {}
    for speaker in speakers:
        old_pk = speaker.pk
        speaker.pk, speaker.activity = None, copy
        speaker.save()
        speaker_map[old_pk] = speaker
    for session in sessions:
        session_speakers = [speaker_map[s.pk] for s in session.speakers.all() if s.pk in speaker_map]
        session.pk, session.activity = None, copy
        session.start_at += delta
        session.end_at += delta
        session.check_in_open = False
        session.save()
        session.speakers.set(session_speakers)
    for question in questions:
        question.pk, question.activity = None, copy
        question.save()
    for category in categories:
        category.pk, category.activity = None, copy
        category.save()
    return copy


@login_required
def activity_duplicate(request, pk):
    activity, allowed = _managed(request, pk)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    suggested_start = activity.start_date + timedelta(days=364)  # mesmo dia da semana, um ano depois
    initial = {'title': _next_title(activity.title), 'start_date': timezone.localtime(suggested_start)}
    form = DuplicateForm(request.POST or None, initial=initial)
    for field in form.fields.values():
        field.widget.attrs['class'] = 'form-control'
    if form.is_valid():
        copy = duplicate_activity(activity, form.cleaned_data['title'], form.cleaned_data['start_date'])
        messages.success(
            request,
            'Nova edição criada como rascunho. Confira datas e textos e mude o status para "Publicado" '
            'quando quiser abrir as inscrições.',
        )
        return redirect('activity_edit', pk=copy.pk)
    return render(
        request,
        'organizer/duplicate.html',
        {
            'form': form,
            'activity': activity,
            'counts': {
                'sessões': activity.sessions.count(),
                'perguntas': activity.questions.count(),
                'palestrantes': activity.speakers.count(),
                'tipos de vaga': activity.categories.count(),
            },
        },
    )


def _next_title(title):
    """ "Semana de Engenharia 2026" vira "Semana de Engenharia 2027"."""
    words = title.split()
    for i in range(len(words) - 1, -1, -1):
        if words[i].isdigit() and len(words[i]) == 4:
            words[i] = str(int(words[i]) + 1)
            return ' '.join(words)
    return f'{title} (nova edição)'


# ---------- Materiais pós-evento


def participated(registration):
    return Attendance.objects.filter(registration=registration).exists()


def can_access_material(user, material):
    """A equipe sempre acessa. O participante, depois da liberação e conforme o público do material."""
    activity = material.activity
    if can_manage(user, activity):
        return True
    if not user.is_authenticated or activity.status == Activity.Status.CANCELLED or not material.is_released():
        return False
    registration = Registration.objects.filter(user=user, activity=activity, status=CONFIRMED).first()
    if registration is None:
        return False
    if material.audience == Material.Audience.ATTENDED and activity.sessions.exists():
        return participated(registration)
    return True


def materials_for(user, activity, registration=None, attended=None):
    """Materiais que a pessoa pode abrir. Mesma regra de can_access_material, em poucas consultas no total
    (antes eram três por material). Quem já tem em mãos a inscrição confirmada e se a pessoa participou
    (ex.: "Minhas inscrições", DES-03) passa os dois e evita mais consultas por atividade."""
    materials = list(activity.materials.all())
    if not materials or can_manage(user, activity):
        return materials
    if not user.is_authenticated or activity.status == Activity.Status.CANCELLED:
        return []
    if registration is None:
        registration = Registration.objects.filter(user=user, activity=activity, status=CONFIRMED).first()
    if registration is None:
        return []
    released = []
    for material in materials:
        if not material.is_released():
            continue
        if material.audience == Material.Audience.ATTENDED:
            if attended is None:
                attended = not activity.sessions.exists() or participated(registration)
            if not attended:
                continue
        released.append(material)
    return released


@login_required
def material_create(request, activity_id):
    activity, allowed = _managed(request, activity_id)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    form = MaterialForm(request.POST or None, request.FILES or None, activity=activity)
    if form.is_valid():
        form.save()
        messages.success(request, 'Material adicionado.')
        return redirect('activity_manage', pk=activity.pk)
    return render(request, 'organizer/simple_form.html', {'form': form, 'activity': activity, **MATERIAL_TEXT})


@login_required
def material_edit(request, pk):
    material = get_object_or_404(Material.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, material.activity):
        raise PermissionDenied('Acesso restrito.')
    form = MaterialForm(request.POST or None, request.FILES or None, instance=material)
    if form.is_valid():
        form.save()
        messages.success(request, 'Material atualizado.')
        return redirect('activity_manage', pk=material.activity_id)
    return render(
        request,
        'organizer/simple_form.html',
        {'form': form, 'activity': material.activity, **MATERIAL_TEXT, 'heading': 'Editar material',
         'current_file': material.file.name.split('/')[-1] if material.file else ''},
    )  # fmt: skip


@login_required
def material_delete(request, pk):
    material = get_object_or_404(Material.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, material.activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method == 'POST':
        if material.file:
            material.file.delete(save=False)
        material.delete()
        messages.success(request, 'Material removido.')
        return redirect('activity_manage', pk=material.activity_id)
    return render(
        request,
        'organizer/confirm_delete.html',
        {'object': material, 'heading': 'Remover material',
         'cancel_url': reverse('activity_manage', args=[material.activity_id])},
    )  # fmt: skip


@login_required
def material_open(request, pk):
    """Entrega o arquivo (ou redireciona para o link) só para quem tem acesso."""
    material = get_object_or_404(Material.objects.select_related('activity'), pk=pk)
    if not can_access_material(request.user, material):
        raise Http404
    if material.url:
        return redirect(material.url)
    try:
        handle = material.file.open('rb')
    except FileNotFoundError as exc:
        raise Http404 from exc
    name = material.file.name.split('/')[-1]
    content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    # Só PDF e imagens abrem no navegador; o resto é baixado. Nada é interpretado como página do site.
    inline = content_type in INLINE_TYPES
    response = FileResponse(
        handle,
        as_attachment=not inline,
        filename=name,
        content_type=content_type if inline else 'application/octet-stream',
    )
    if content_type != 'application/pdf':  # o leitor de PDF do Chrome não abre com sandbox
        response['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    response['X-Content-Type-Options'] = 'nosniff'
    return response


INLINE_TYPES = {'application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp'}


MATERIAL_TEXT = {
    'heading': 'Novo material',
    'intro': 'Slides, apostila ou gravação. O arquivo não fica público: só quem tem acesso consegue abrir.',
}


# ---------- Crachás em PDF

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
    activity, allowed = _managed(request, pk)
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


# ---------- Números da atividade


def _percent(part, whole):
    return round(part * 100 / whole) if whole else 0


def activity_numbers(activity):
    regs = list(activity.registrations.select_related('category', 'user'))
    confirmed = [r for r in regs if r.status == CONFIRMED]
    waitlisted = [r for r in regs if r.status == Registration.Status.WAITLISTED]
    cancelled = [r for r in regs if r.status == Registration.Status.CANCELLED]
    session_count = activity.sessions.count()
    attended_ids = set(
        Attendance.objects.filter(registration__activity=activity).values_list('registration_id', flat=True)
    )
    attended = [r for r in confirmed if r.pk in attended_ids]
    attendance_total = Attendance.objects.filter(
        registration__activity=activity, registration__status=CONFIRMED
    ).count()
    scores = list(SurveyResponse.objects.filter(registration__activity=activity).values_list('score', flat=True))

    # Inscrições e desistências por dia
    local = timezone.localtime
    days = Counter(local(r.created_at).date() for r in regs)
    drops = Counter(local(r.cancelled_at).date() for r in cancelled if r.cancelled_at)
    timeline = []
    if days:
        start, end = min(days), max([*days, *drops]) if drops else max(days)
        peak = max([*days.values(), *drops.values(), 1])
        running, day = 0, start
        while day <= end and len(timeline) < 120:
            running += days.get(day, 0)
            new, dropped = days.get(day, 0), drops.get(day, 0)
            timeline.append(
                {
                    'day': day,
                    'new': new,
                    'dropped': dropped,
                    'total': running,
                    'h_new': round(new * 100 / peak),
                    'h_drop': round(dropped * 100 / peak),
                }
            )
            day += timedelta(days=1)

    # Origem: tipo de vaga, aluno x externo e respostas das perguntas de escolha
    breakdowns = []
    if activity.categories.exists():
        counter = Counter(r.category.name if r.category_id else 'Sem tipo' for r in confirmed)
        breakdowns.append(_breakdown('Tipo de vaga', counter, len(confirmed)))
    site = SiteSettings.load()
    counter = Counter('Aluno (matrícula confirmada)' if r.user.student_id_confirmed_in(site) else 'Externo'
                      for r in confirmed)  # fmt: skip
    breakdowns.append(_breakdown('Aluno ou externo', counter, len(confirmed)))
    choice_questions = activity.questions.filter(
        kind__in=[RegistrationQuestion.Kind.CHOICE, RegistrationQuestion.Kind.YES_NO]
    )
    confirmed_ids = {r.pk for r in confirmed}
    for question in choice_questions:
        answers = RegistrationAnswer.objects.filter(question=question, registration_id__in=confirmed_ids)
        counter = Counter(a.value or 'Sem resposta' for a in answers)
        missing = len(confirmed) - sum(counter.values())
        if missing:
            counter['Sem resposta'] += missing
        breakdowns.append(_breakdown(question.label, counter, len(confirmed)))

    return {
        'registered': len(regs),
        'confirmed': len(confirmed),
        'waitlisted': len(waitlisted),
        'cancelled': len(cancelled),
        'occupancy': _percent(len(confirmed), activity.capacity),
        'dropout': _percent(len(cancelled), len(regs)),
        'session_count': session_count,
        'attended': len(attended),
        'attendance_rate': _percent(len(attended), len(confirmed)),
        'avg_frequency': _percent(attendance_total, len(confirmed) * session_count) if session_count else None,
        'certificates': activity.registrations.filter(
            certificate__isnull=False, certificate__revoked_at__isnull=True
        ).count(),
        'survey_avg': round(sum(scores) / len(scores), 1) if scores else None,
        'survey_count': len(scores),
        'timeline': timeline,
        'breakdowns': breakdowns,
    }


def _breakdown(title, counter, total):
    peak = max(counter.values(), default=1) or 1
    items = [{'label': label, 'count': n, 'percent': _percent(n, total), 'width': round(n * 100 / peak)}
             for label, n in counter.most_common()]  # fmt: skip
    return {'title': title, 'items': items}


def editions(activity, limit=8):
    """A edição atual e as anteriores (ligadas por "Duplicar"), da mais antiga para a mais nova."""
    chain, seen, current = [], set(), activity
    while current is not None and current.pk not in seen and len(chain) < limit:
        chain.append(current)
        seen.add(current.pk)
        current = current.previous_edition
    later = list(activity.next_editions.exclude(pk__in=seen)[:3])
    return list(reversed(chain)) + later


@login_required
def activity_stats(request, pk):
    activity, allowed = _managed(request, pk)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    series = editions(activity)
    comparison = []
    if len(series) > 1:
        for edition in series:
            numbers = activity_numbers(edition)
            comparison.append({'activity': edition, **numbers, 'is_current': edition.pk == activity.pk})
    return render(
        request,
        'organizer/stats.html',
        {'activity': activity, 'n': activity_numbers(activity), 'comparison': comparison},
    )
