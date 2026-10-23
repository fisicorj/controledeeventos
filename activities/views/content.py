"""Conteúdo da atividade: sessões, perguntas da inscrição, palestrantes e tipos de vaga."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.permissions import can_manage
from registrations import notifications, services, sessions
from registrations.models import Registration

from ..forms import CategoryForm, QuestionForm, SessionForm, SpeakerForm
from ..models import Activity, RegistrationQuestion, Session, Speaker, SpotCategory


def session_certificates(session):
    from certificates.models import Certificate

    return Certificate.objects.filter(session_enrollment__session=session)


SESSION_NOTIFY_FIELDS = ('start_at', 'end_at', 'location')


def _announce_session_change(session, changes):
    """Horário ou local de uma sessão futura mudou: avisa quem vai a ela. Devolve quantos foram avisados."""
    if not changes or session.end_at <= timezone.now():
        return 0
    if session.activity.per_session:
        people = [
            e.registration
            for e in session.enrollments.filter(status__in=sessions.ACTIVE).select_related(
                'registration__user', 'registration__activity'
            )
        ]
    else:
        people = list(
            session.activity.registrations.filter(
                status__in=[Registration.Status.CONFIRMED, Registration.Status.WAITLISTED]
            ).select_related('user', 'activity')
        )
    notifications.notify_session_changed(session, people, changes)
    return len(people)


@login_required
def session_create(request, activity_id):
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    form = SessionForm(request.POST or None, activity=activity)
    if form.is_valid():
        session = form.save(commit=False)
        session.activity = activity
        session.save()
        messages.success(request, 'Sessão criada.')
        return redirect('activity_manage', pk=activity.id)
    return render(request, 'organizer/session_form.html', {'form': form, 'activity': activity})


@login_required
def session_edit(request, pk):
    session = get_object_or_404(Session.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, session.activity):
        raise PermissionDenied('Acesso restrito.')
    before = {field: getattr(session, field) for field in SESSION_NOTIFY_FIELDS}
    form = SessionForm(request.POST or None, instance=session)
    if form.is_valid():
        with transaction.atomic():
            services.lock_activity(session.activity_id)
            form.save()
            # Mais vagas na sessão: a fila dela é chamada na hora.
            promoted = sessions.promote(session) if session.activity.per_session else []
            changes = [
                (f, before[f], getattr(session, f)) for f in SESSION_NOTIFY_FIELDS if before[f] != getattr(session, f)
            ]
            notified = _announce_session_change(session, changes) if form.cleaned_data.get('notify_change') else 0
            if any(field == 'start_at' for field, _, _ in changes):
                session.enrollments.update(reminder_sent_at=None)  # o lembrete vale para o horário novo
        messages.success(request, 'Sessão atualizada.')
        if notified:
            messages.info(request, f'{notified} pessoa(s) avisada(s) da mudança por e-mail.')
        if changes and session.activity.per_session:
            clashes = sessions.clashes(session)
            if clashes:
                messages.warning(
                    request,
                    f'{clashes} participante(s) desta sessão agora têm outra sessão escolhida no mesmo horário. '
                    'Eles podem trocar em "Minhas inscrições"; avise-os, se for o caso.',
                )
        if promoted:
            messages.info(request, f'{len(promoted)} pessoa(s) da lista de espera da sessão foram confirmadas.')
        return redirect('activity_manage', pk=session.activity_id)
    return render(
        request,
        'organizer/session_form.html',
        {
            'form': form,
            'activity': session.activity,
            'session': session,
            'heading': 'Editar sessão',
        },
    )


@login_required
def session_delete(request, pk):
    session = get_object_or_404(Session.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, session.activity):
        raise PermissionDenied('Acesso restrito.')
    if session_certificates(session).exists():
        messages.error(
            request,
            'Esta sessão já tem certificados emitidos e não pode ser excluída. Se precisar, revogue os certificados.',
        )
        return redirect('activity_manage', pk=session.activity_id)
    chosen = session.enrollments.filter(status__in=sessions.ACTIVE)
    if request.method == 'POST':
        activity_id = session.activity_id
        with transaction.atomic():
            services.lock_activity(activity_id)
            people = [e.registration for e in chosen.select_related('registration__user', 'registration__activity')]
            if session.start_at > timezone.now():
                notifications.notify_session_removed(session, people)
            session.delete()
        messages.success(request, 'Sessão excluída.')
        if people and session.start_at > timezone.now():
            messages.info(request, f'{len(people)} participante(s) que tinham escolhido a sessão foram avisados.')
        return redirect('activity_manage', pk=activity_id)
    warning = 'As presenças registradas nesta sessão também serão excluídas.'
    if count := chosen.count():
        warning += f' {count} participante(s) escolheram esta sessão e serão avisados por e-mail.'
    return render(
        request,
        'organizer/confirm_delete.html',
        {
            'object': session,
            'heading': 'Excluir sessão',
            'warning': warning,
            'cancel_url': reverse('activity_manage', args=[session.activity_id]),
        },
    )


@login_required
def question_create(request, activity_id):
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    form = QuestionForm(request.POST or None, activity=activity)
    if form.is_valid():
        form.save()
        messages.success(request, 'Pergunta adicionada ao formulário de inscrição.')
        return redirect('activity_manage', pk=activity.id)
    return render(request, 'organizer/question_form.html', {'form': form, 'activity': activity})


@login_required
def question_edit(request, pk):
    question = get_object_or_404(RegistrationQuestion.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, question.activity):
        raise PermissionDenied('Acesso restrito.')
    form = QuestionForm(request.POST or None, instance=question)
    if form.is_valid():
        form.save()
        messages.success(request, 'Pergunta atualizada.')
        return redirect('activity_manage', pk=question.activity_id)
    return render(
        request,
        'organizer/question_form.html',
        {
            'form': form,
            'activity': question.activity,
            'heading': 'Editar pergunta',
            'answered': question.answers.exists(),
        },
    )


@login_required
def question_delete(request, pk):
    question = get_object_or_404(RegistrationQuestion.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, question.activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method == 'POST':
        question.delete()
        messages.success(request, 'Pergunta excluída.')
        return redirect('activity_manage', pk=question.activity_id)
    answers = question.answers.count()
    return render(
        request,
        'organizer/confirm_delete.html',
        {
            'object': question,
            'heading': 'Excluir pergunta',
            'warning': f'As {answers} resposta(s) já dadas a esta pergunta também serão excluídas.' if answers else '',
            'cancel_url': reverse('activity_manage', args=[question.activity_id]),
        },
    )


@login_required
def speaker_create(request, activity_id):
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    form = SpeakerForm(request.POST or None, request.FILES or None, activity=activity)
    if form.is_valid():
        form.save()
        messages.success(request, 'Palestrante adicionado.')
        return redirect('activity_manage', pk=activity.id)
    return render(request, 'organizer/speaker_form.html', {'form': form, 'activity': activity})


@login_required
def speaker_edit(request, pk):
    speaker = get_object_or_404(Speaker.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, speaker.activity):
        raise PermissionDenied('Acesso restrito.')
    form = SpeakerForm(request.POST or None, request.FILES or None, instance=speaker)
    if form.is_valid():
        form.save()
        messages.success(request, 'Palestrante atualizado.')
        return redirect('activity_manage', pk=speaker.activity_id)
    return render(
        request,
        'organizer/speaker_form.html',
        {'form': form, 'activity': speaker.activity, 'speaker': speaker, 'heading': 'Editar palestrante'},
    )


@login_required
def speaker_delete(request, pk):
    speaker = get_object_or_404(Speaker.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, speaker.activity):
        raise PermissionDenied('Acesso restrito.')
    if hasattr(speaker, 'certificate'):
        messages.error(
            request,
            f'{speaker.name} já tem certificado de palestrante ({speaker.certificate.code}) e não pode ser removido. '
            'Se precisar, revogue o certificado.',
        )
        return redirect('activity_manage', pk=speaker.activity_id)
    if request.method == 'POST':
        speaker.delete()
        messages.success(request, 'Palestrante removido.')
        return redirect('activity_manage', pk=speaker.activity_id)
    return render(
        request,
        'organizer/confirm_delete.html',
        {
            'object': speaker,
            'heading': 'Remover palestrante',
            'cancel_url': reverse('activity_manage', args=[speaker.activity_id]),
        },
    )


def categories_with_counts(activity):
    categories = list(activity.categories.all())
    counts = services.category_counts(activity) if categories else {}
    waiting = dict(
        activity.registrations.filter(status=Registration.Status.WAITLISTED)
        .values_list('category_id')
        .annotate(n=Count('id'))
    )
    for category in categories:
        category.taken = counts.get(category.id, 0)
        category.waiting = waiting.get(category.id, 0)
    return categories


CATEGORY_TEXT = {
    'heading': 'Novo tipo de vaga',
    'intro': 'Divide as vagas da atividade. Cada tipo tem a sua própria quantidade e a sua própria lista de espera. '
    'O participante escolhe o tipo ao se inscrever.',
}


@login_required
def category_create(request, activity_id):
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    form = CategoryForm(request.POST or None, activity=activity)
    if form.is_valid():
        form.save()
        messages.success(request, 'Tipo de vaga criado.')
        return redirect('activity_manage', pk=activity.id)
    return render(request, 'organizer/simple_form.html', {'form': form, 'activity': activity, **CATEGORY_TEXT})


@login_required
def category_edit(request, pk):
    category = get_object_or_404(SpotCategory.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, category.activity):
        raise PermissionDenied('Acesso restrito.')
    form = CategoryForm(request.POST or None, instance=category)
    if form.is_valid():
        with transaction.atomic():
            activity = services.lock_activity(category.activity_id)
            form.save()
            services.promote_waitlist(activity)  # mais vagas no tipo: chama a fila
        messages.success(request, 'Tipo de vaga atualizado.')
        return redirect('activity_manage', pk=category.activity_id)
    return render(
        request,
        'organizer/simple_form.html',
        {'form': form, 'activity': category.activity, **CATEGORY_TEXT, 'heading': 'Editar tipo de vaga'},
    )


@login_required
def category_delete(request, pk):
    category = get_object_or_404(SpotCategory.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, category.activity):
        raise PermissionDenied('Acesso restrito.')
    if category.registrations.exists():
        messages.error(
            request, 'Este tipo de vaga já tem inscrições e não pode ser excluído. Edite as vagas, se precisar.'
        )
        return redirect('activity_manage', pk=category.activity_id)
    if request.method == 'POST':
        category.delete()
        messages.success(request, 'Tipo de vaga excluído.')
        return redirect('activity_manage', pk=category.activity_id)
    return render(
        request,
        'organizer/confirm_delete.html',
        {
            'object': category,
            'heading': 'Excluir tipo de vaga',
            'cancel_url': reverse('activity_manage', args=[category.activity_id]),
        },
    )
