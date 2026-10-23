"""Painel da instituição (O-03): os números de todas as atividades de um semestre, para a coordenação acadêmica.

O administrador vê todas as atividades; o organizador, as próprias. As contas são feitas com poucas consultas
agrupadas (uma por indicador), não atividade por atividade, para o painel abrir rápido mesmo com o semestre cheio.
"""

import io

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from attendance.models import Attendance
from core.csvsafe import excel_writer
from core.pagination import semester_choices, semester_of, semester_range
from core.permissions import activities_for, is_organizer, is_platform_admin
from registrations.models import Registration, SurveyResponse

from ..models import Activity

CONFIRMED = Registration.Status.CONFIRMED


def _percent(part, whole):
    return round(part * 100 / whole) if whole else None


def _by_activity(queryset, key, **aggregates):
    return {row[key]: row for row in queryset.values(key).annotate(**aggregates)}


def semester_numbers(activities):
    """Linha por atividade e totais do semestre. activities: atividades do semestre, sem rascunhos."""
    activities = list(activities.select_related('organizer').order_by('start_date', 'pk'))
    ids = [activity.pk for activity in activities]
    regs = Registration.objects.filter(activity_id__in=ids, status=CONFIRMED)
    confirmed = _by_activity(regs, 'activity_id', n=Count('id'))
    attended = _by_activity(
        Attendance.objects.filter(registration__activity_id__in=ids, registration__status=CONFIRMED),
        'registration__activity_id',
        n=Count('registration_id', distinct=True),
    )
    survey = _by_activity(
        SurveyResponse.objects.filter(registration__activity_id__in=ids),
        'registration__activity_id',
        n=Count('id'),
        avg=Avg('score'),
        promoters=Count('id', filter=Q(score__gte=9)),
        detractors=Count('id', filter=Q(score__lte=6)),
    )
    certificates = _certificates(ids)
    now = timezone.now()
    rows = []
    for activity in activities:
        n_confirmed = confirmed.get(activity.pk, {}).get('n', 0)
        n_attended = attended.get(activity.pk, {}).get('n', 0)
        started = activity.start_date <= now and activity.status != Activity.Status.CANCELLED
        answers = survey.get(activity.pk, {})
        certs = certificates.get(activity.pk, {})
        rows.append(
            {
                'activity': activity,
                'confirmed': n_confirmed,
                'attended': n_attended,
                'started': started,
                'attendance_rate': _percent(n_attended, n_confirmed) if started else None,
                'certificates': certs.get('n', 0),
                'hours': certs.get('hours', 0),
                'survey_count': answers.get('n', 0),
                'survey_avg': round(answers['avg'], 1) if answers.get('avg') is not None else None,
            }
        )
    valid = [row for row in rows if row['activity'].status != Activity.Status.CANCELLED]
    started = [row for row in valid if row['started']]
    survey_total = sum(row.get('n', 0) for row in survey.values())
    promoters = sum(row['promoters'] for row in survey.values())
    detractors = sum(row['detractors'] for row in survey.values())
    score_sum = sum(row['avg'] * row['n'] for row in survey.values() if row['avg'] is not None)
    by_type = {}
    for row in valid:
        label = row['activity'].get_activity_type_display()
        item = by_type.setdefault(label, {'label': label, 'activities': 0, 'confirmed': 0, 'hours': 0})
        item['activities'] += 1
        item['confirmed'] += row['confirmed']
        item['hours'] += row['hours']
    totals = {
        'activities': len(valid),
        'cancelled': len(rows) - len(valid),
        'confirmed': sum(row['confirmed'] for row in valid),
        'people': regs.exclude(activity__status=Activity.Status.CANCELLED).values('user_id').distinct().count(),
        'attendance_rate': _percent(sum(r['attended'] for r in started), sum(r['confirmed'] for r in started)),
        'certificates': sum(row['certificates'] for row in rows),
        'hours': sum(row['hours'] for row in rows),
        'survey_count': survey_total,
        'survey_avg': round(score_sum / survey_total, 1) if survey_total else None,
        'nps': round((promoters - detractors) * 100 / survey_total) if survey_total else None,
    }
    return rows, totals, sorted(by_type.values(), key=lambda item: -item['activities'])


def _certificates(ids):
    """Certificados válidos e horas certificadas por atividade: participação (por inscrição ou por sessão),
    palestrante e apresentação de trabalho."""
    from certificates.models import Certificate

    valid = Certificate.objects.filter(revoked_at__isnull=True)
    sources = [
        ('registration__activity_id', Q(registration__activity_id__in=ids)),
        ('session_enrollment__session__activity_id', Q(session_enrollment__session__activity_id__in=ids)),
        ('speaker__activity_id', Q(speaker__activity_id__in=ids)),
        ('submission__call__activity_id', Q(submission__call__activity_id__in=ids)),
    ]
    result = {}
    for key, condition in sources:
        rows = valid.filter(condition).values(key).annotate(n=Count('id'), hours=Coalesce(Sum('workload_hours'), 0))
        for row in rows:
            item = result.setdefault(row[key], {'n': 0, 'hours': 0})
            item['n'] += row['n']
            item['hours'] += row['hours']
    return result


@login_required
def institution_panel(request):
    if not is_organizer(request.user):
        raise PermissionDenied('Acesso restrito.')
    scope = activities_for(request.user, Activity.objects.exclude(status=Activity.Status.DRAFT))
    period = request.GET.get('semestre', '')
    bounds = semester_range(period)
    if bounds is None:
        period = semester_of(timezone.now())
        bounds = semester_range(period)
    rows, totals, by_type = semester_numbers(scope.filter(start_date__gte=bounds[0], start_date__lt=bounds[1]))
    choices = semester_choices([*scope.datetimes('start_date', 'month'), timezone.now()])
    if request.GET.get('formato') == 'csv':
        return _csv(rows, period)
    return render(
        request,
        'organizer/institution.html',
        {
            'rows': rows,
            'totals': totals,
            'by_type': by_type,
            'period': period,
            'period_label': period.replace('-', '/'),
            'periods': choices,
            'all_activities': is_platform_admin(request.user),
        },
    )


def _csv(rows, period):
    buffer = io.StringIO()
    writer = excel_writer(buffer)
    writer.writerow([
        'Atividade', 'Tipo', 'Situação', 'Início', 'Organização', 'Inscritos confirmados', 'Compareceram',
        'Comparecimento (%)', 'Certificados', 'Horas certificadas', 'Respostas da pesquisa', 'Nota média',
    ])  # fmt: skip
    for row in rows:
        activity = row['activity']
        writer.writerow([
            activity.title, activity.get_activity_type_display(), activity.get_status_display(),
            timezone.localtime(activity.start_date).strftime('%d/%m/%Y'),
            activity.organizer.get_full_name() or activity.organizer.username,
            row['confirmed'], row['attended'], '' if row['attendance_rate'] is None else row['attendance_rate'],
            row['certificates'], row['hours'], row['survey_count'],
            '' if row['survey_avg'] is None else str(row['survey_avg']).replace('.', ','),
        ])  # fmt: skip
    response = HttpResponse('﻿' + buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="atividades-{period}.csv"'
    return response
