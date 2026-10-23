"""Números da atividade: inscrições, presença, certificados e pesquisa."""

from collections import Counter
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

from attendance.models import Attendance
from core.models import SiteSettings
from registrations.models import Registration, RegistrationAnswer, SurveyResponse

from ..models import RegistrationQuestion
from .common import CONFIRMED, managed_activity


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
        'avg_frequency': _avg_frequency(activity, confirmed, attendance_total, session_count),
        'certificates': _valid_certificates(activity),
        'role_certificates': _valid_role_certificates(activity),
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
    activity, allowed = managed_activity(request, pk)
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


def _valid_certificates(activity):
    """Certificados válidos da atividade (um por inscrito, ou um por sessão na atividade por sessão)."""
    from django.db.models import Q

    from certificates.models import Certificate

    return Certificate.objects.filter(
        Q(registration__activity=activity) | Q(session_enrollment__session__activity=activity),
        revoked_at__isnull=True,
    ).count()


def _avg_frequency(activity, confirmed, attendance_total, session_count):
    """Frequência média. Na atividade por sessão, sobre as sessões que cada pessoa escolheu (e não sobre todas)."""
    if not session_count:
        return None
    if not activity.per_session:
        return _percent(attendance_total, len(confirmed) * session_count)
    from registrations.models import SessionEnrollment

    chosen = set(
        SessionEnrollment.objects.filter(
            session__activity=activity,
            status=SessionEnrollment.Status.CONFIRMED,
            registration__status=CONFIRMED,
        ).values_list('registration_id', 'session_id')
    )
    present = set(
        Attendance.objects.filter(session__activity=activity, registration__status=CONFIRMED).values_list(
            'registration_id', 'session_id'
        )
    )
    return _percent(len(chosen & present), len(chosen)) if chosen else None


def _valid_role_certificates(activity):
    """Certificados válidos de palestrante e de apresentação de trabalho."""
    from certificates.roles import role_certificates

    return role_certificates(activity).filter(revoked_at__isnull=True).count()
