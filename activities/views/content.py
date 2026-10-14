"""Conteúdo da atividade: sessões, perguntas da inscrição, palestrantes e tipos de vaga."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.permissions import can_manage
from registrations import services
from registrations.models import Registration

from ..forms import CategoryForm, QuestionForm, SessionForm, SpeakerForm
from ..models import Activity, RegistrationQuestion, Session, Speaker, SpotCategory


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
    form = SessionForm(request.POST or None, instance=session)
    if form.is_valid():
        form.save()
        messages.success(request, 'Sessão atualizada.')
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
    if request.method == 'POST':
        activity_id = session.activity_id
        session.delete()
        messages.success(request, 'Sessão excluída.')
        return redirect('activity_manage', pk=activity_id)
    return render(
        request,
        'organizer/confirm_delete.html',
        {
            'object': session,
            'heading': 'Excluir sessão',
            'warning': 'As presenças registradas nesta sessão também serão excluídas.',
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
