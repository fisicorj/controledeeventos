"""Duplicar uma atividade para a próxima edição."""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from ..forms import DuplicateForm
from ..models import Activity
from .common import managed_activity


@transaction.atomic
def duplicate_activity(activity, title, start_date):
    """Copia a atividade como rascunho, deslocando todas as datas pelo mesmo intervalo.
    Vão junto: sessões (e quem fala em cada uma), perguntas, palestrantes, tipos de vaga,
    configuração de certificado e pesquisa, e a chamada de trabalhos (regras, áreas, critérios, avaliadores com
    as áreas deles e coordenação). Não vão: inscrições, comunicados, materiais e trabalhos enviados."""
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
    _copy_call(activity, copy, delta)
    return copy


def _copy_call(activity, copy, delta):
    from submissions.models import CallForPapers, ReviewerAreas

    call = CallForPapers.objects.filter(activity=activity).first()
    if call is None:
        return
    reviewers = list(call.reviewers.all())
    coordinators = list(call.coordinators.all())
    areas = list(ReviewerAreas.objects.filter(call=call))
    call.pk, call.activity = None, copy
    call.opens_at += delta
    call.closes_at += delta
    call.save()
    call.reviewers.set(reviewers)
    call.coordinators.set(coordinators)
    for row in areas:
        ReviewerAreas.objects.create(call=call, reviewer_id=row.reviewer_id, areas=row.areas)


@login_required
def activity_duplicate(request, pk):
    activity, allowed = managed_activity(request, pk)
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
