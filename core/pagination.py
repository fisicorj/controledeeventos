"""Paginação das listagens (parâmetro ?pagina=), mantendo os filtros na troca de página."""

from datetime import datetime

from django.core.paginator import Paginator
from django.utils import timezone


def paginate(request, items, per_page):
    """Devolve (página, querystring dos filtros sem a página) para o templates/core/_pager.html."""
    page = Paginator(items, per_page).get_page(request.GET.get('pagina'))
    params = request.GET.copy()
    params.pop('pagina', None)
    return page, params.urlencode()


# ---------- Semestre (1º: janeiro a junho; 2º: julho a dezembro)


def semester_of(moment):
    local = timezone.localtime(moment)
    return f'{local.year}-{1 if local.month <= 6 else 2}'


def semester_range(value):
    """'2026-2' -> (início, fim) no fuso local; None se o valor não for um semestre."""
    try:
        year, half = (int(part) for part in value.split('-'))
    except (ValueError, AttributeError):
        return None
    if half not in (1, 2) or not 2000 <= year <= 2100:
        return None
    start = datetime(year, 1 if half == 1 else 7, 1)
    end = datetime(year, 7, 1) if half == 1 else datetime(year + 1, 1, 1)
    tz = timezone.get_current_timezone()
    return timezone.make_aware(start, tz), timezone.make_aware(end, tz)


def semester_choices(moments):
    """Semestres (mais recente primeiro) das datas informadas: [('2026-2', '2026/2'), ...]."""
    values = sorted({semester_of(m) for m in moments if m}, reverse=True)
    return [(v, v.replace('-', '/')) for v in values]
