from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from attendance.summary import participation
from certificates.models import Certificate
from certificates.services import finish_activity, issue_certificates
from registrations import notifications, services
from registrations.forms import RegistrationAnswersForm
from registrations.models import Registration

from .forms import ActivityForm, QuestionForm, SessionForm
from .models import Activity, RegistrationQuestion, Session

CONFIRMED = Q(registrations__status=Registration.Status.CONFIRMED)
WAITLISTED = Q(registrations__status=Registration.Status.WAITLISTED)


def with_counts(queryset):
    return queryset.annotate(
        confirmed_count=Count('registrations', filter=CONFIRMED),
        waitlist_count=Count('registrations', filter=WAITLISTED),
    )


def activity_queryset():
    return with_counts(Activity.objects.filter(status=Activity.Status.PUBLISHED))


def upcoming_queryset():
    # Esconde atividades já encerradas; as em andamento continuam visíveis.
    return activity_queryset().filter(end_date__gte=timezone.now()).order_by('start_date')


def home(request):
    upcoming = list(upcoming_queryset()[:9])
    return render(
        request,
        'home.html',
        {'activities': upcoming, 'agenda': upcoming[:4], 'types': Activity.Type.choices},
    )


def activity_list(request):
    activities = upcoming_queryset()
    query = request.GET.get('q', '').strip()
    kind = request.GET.get('tipo', '')
    if query:
        activities = activities.filter(
            Q(title__icontains=query) | Q(description__icontains=query) | Q(location__icontains=query)
        )
    if kind in Activity.Type.values:
        activities = activities.filter(activity_type=kind)
    else:
        kind = ''
    return render(
        request,
        'activities/list.html',
        {'activities': activities, 'query': query, 'kind': kind, 'types': Activity.Type.choices},
    )


def can_manage(user, activity=None):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.role == User.Role.ADMIN:
        return True
    return user.role == User.Role.ORGANIZER and (activity is None or activity.organizer_id == user.id)


def registration_state(activity, registration):
    """Diz se o botão de inscrição deve aparecer e, se não, por quê."""
    if registration and registration.status == Registration.Status.CONFIRMED:
        return {'code': 'registered'}
    if registration and registration.status == Registration.Status.WAITLISTED:
        return {'code': 'waitlisted', 'position': registration.waitlist_position()}
    if activity.status != Activity.Status.PUBLISHED:
        return {'code': 'blocked', 'reason': f'Atividade {activity.get_status_display().lower()}.'}
    now = timezone.now()
    if now < activity.registration_start:
        start = timezone.localtime(activity.registration_start).strftime('%d/%m/%Y às %H:%M')
        return {'code': 'blocked', 'reason': f'Inscrições abrem em {start}.'}
    if now > activity.registration_end:
        return {'code': 'blocked', 'reason': 'Inscrições encerradas.'}
    if activity.confirmed_count >= activity.capacity:
        if activity.waitlist_enabled:
            return {'code': 'waitlist'}
        return {'code': 'blocked', 'reason': 'Vagas esgotadas.'}
    return {'code': 'open'}


def activity_detail(request, pk, answers_form=None):
    activity = with_counts(Activity.objects.prefetch_related('sessions')).filter(pk=pk).first()
    if activity is None:
        raise Http404
    # Rascunhos, canceladas e finalizadas só aparecem para quem gerencia (pré-visualização).
    is_preview = activity.status != Activity.Status.PUBLISHED
    if is_preview and not can_manage(request.user, activity):
        raise Http404
    registration = None
    if request.user.is_authenticated:
        registration = activity.registrations.filter(user=request.user).first()
    if answers_form is None:
        # Quem cancelou e vai se inscrever de novo encontra as respostas anteriores já preenchidas.
        previous = {a.question_id: a.value for a in registration.answers.all()} if registration else {}
        answers_form = RegistrationAnswersForm(activity.questions.all(), initial_answers=previous)
    return render(
        request,
        'activities/detail.html',
        {
            'activity': activity,
            'registration': registration,
            'state': registration_state(activity, registration),
            'is_preview': is_preview,
            'answers_form': answers_form,
        },
    )


@login_required
def organizer_dashboard(request):
    if not can_manage(request.user):
        return HttpResponseForbidden('Acesso restrito a organizadores e administradores.')
    if request.user.is_superuser or request.user.role == User.Role.ADMIN:
        qs = Activity.objects.all()
    else:
        qs = Activity.objects.filter(organizer=request.user)
    qs = with_counts(qs).annotate(registration_total=Count('registrations')).order_by('-created_at')
    return render(request, 'organizer/dashboard.html', {'activities': qs})


@login_required
def activity_create(request):
    if not can_manage(request.user):
        return HttpResponseForbidden('Acesso restrito.')
    form = ActivityForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        activity = form.save(commit=False)
        activity.organizer = request.user
        activity.save()
        messages.success(request, 'Atividade criada.')
        return redirect('organizer_dashboard')
    return render(request, 'organizer/activity_form.html', {'form': form, 'heading': 'Nova atividade'})


@login_required
def activity_edit(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
    old_status = activity.status
    form = ActivityForm(request.POST or None, request.FILES or None, instance=activity)
    if form.is_valid():
        with transaction.atomic():
            services.lock_activity(activity.pk)
            activity = form.save()
            new_status = activity.status
            if new_status == Activity.Status.CANCELLED and old_status != new_status:
                _cancel(activity)
            elif new_status == Activity.Status.FINISHED and old_status != new_status:
                issued = finish_activity(activity, request=request)
                messages.info(request, f'Atividade finalizada. {len(issued)} certificado(s) emitido(s).')
            else:
                # Se a capacidade aumentou, a fila é chamada na hora.
                promoted = services.promote_waitlist(activity)
                if promoted:
                    messages.info(request, f'{len(promoted)} pessoa(s) da lista de espera foram confirmadas.')
        messages.success(request, 'Atividade atualizada.')
        return redirect('organizer_dashboard')
    return render(
        request,
        'organizer/activity_form.html',
        {
            'form': form,
            'heading': 'Editar atividade',
            'activity': activity,
        },
    )


@login_required
def activity_delete(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
    if activity.registrations.exists():
        messages.error(
            request,
            'Esta atividade já possui inscrições e não pode ser excluída. '
            'Use "Cancelar" para encerrá-la preservando o histórico.',
        )
        return redirect('organizer_dashboard')
    if request.method == 'POST':
        activity.delete()
        messages.success(request, 'Atividade excluída.')
        return redirect('organizer_dashboard')
    return render(request, 'organizer/confirm_delete.html', {'object': activity})


@login_required
def activity_cancel(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
    if activity.status == Activity.Status.CANCELLED:
        messages.info(request, 'Esta atividade já está cancelada.')
        return redirect('organizer_dashboard')
    if request.method == 'POST':
        with transaction.atomic():
            activity = services.lock_activity(activity.pk)
            activity.status = Activity.Status.CANCELLED
            activity.save(update_fields=['status', 'updated_at'])
            _cancel(activity)
        messages.success(
            request,
            'Atividade cancelada. Os inscritos foram avisados por e-mail; inscrições e presenças foram preservadas.',
        )
        return redirect('organizer_dashboard')
    confirmed = activity.registrations.filter(status=Registration.Status.CONFIRMED).count()
    return render(request, 'organizer/confirm_cancel.html', {'object': activity, 'confirmed': confirmed})


def _cancel(activity):
    """Efeitos do cancelamento: fecha o check-in e avisa inscritos e quem estava na fila."""
    activity.sessions.update(check_in_open=False)
    to_notify = activity.registrations.filter(
        status__in=[Registration.Status.CONFIRMED, Registration.Status.WAITLISTED]
    ).select_related('user', 'activity')
    notifications.notify_activity_cancelled(activity, to_notify)


@login_required
def activity_finish(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
    if activity.status != Activity.Status.PUBLISHED:
        messages.info(request, 'Só atividades publicadas podem ser finalizadas.')
        return redirect('activity_manage', pk=activity.pk)
    if request.method == 'POST':
        issued = finish_activity(activity, request=request)
        messages.success(
            request,
            f'Atividade finalizada. {len(issued)} certificado(s) emitido(s) e enviado(s) por e-mail.'
            if activity.certificate_enabled
            else 'Atividade finalizada.',
        )
        return redirect('activity_manage', pk=activity.pk)
    sessions, rows = participation(activity)
    eligible = sum(1 for r in rows if r.percent >= activity.min_attendance_percent) if sessions else 0
    return render(
        request,
        'organizer/confirm_finish.html',
        {'activity': activity, 'sessions': sessions, 'total': len(rows), 'eligible': eligible},
    )


@login_required
def certificates_issue(request, pk):
    """Emite os certificados que ficaram pendentes (ex.: presença lançada depois de finalizar)."""
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
    if request.method == 'POST' and activity.status == Activity.Status.FINISHED:
        with transaction.atomic():
            issued = issue_certificates(activity, request=request)
        messages.success(request, f'{len(issued)} certificado(s) novo(s) emitido(s).')
    return redirect('activity_manage', pk=activity.pk)


@login_required
def session_create(request, activity_id):
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
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
        return HttpResponseForbidden('Acesso restrito.')
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
        return HttpResponseForbidden('Acesso restrito.')
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
def activity_manage(request, pk):
    activity = get_object_or_404(Activity.objects.prefetch_related('sessions'), pk=pk)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
    # Frequência calculada em consultas fixas (antes era uma consulta por inscrito).
    sessions, rows = participation(activity)
    certified = set(
        Certificate.objects.filter(registration__activity=activity).values_list('registration_id', flat=True)
    )
    for row in rows:
        row.certified = row.registration.id in certified
    waitlist = (
        activity.registrations.filter(status=Registration.Status.WAITLISTED)
        .select_related('user')
        .order_by('waitlisted_at', 'id')
    )
    return render(
        request,
        'organizer/activity_manage.html',
        {
            'activity': activity,
            'rows': rows,
            'session_count': len(sessions),
            'waitlist': waitlist,
            'questions': activity.questions.all(),
            'certified_count': len(certified),
        },
    )


# ---------- Perguntas do formulário de inscrição


@login_required
def question_create(request, activity_id):
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        return HttpResponseForbidden('Acesso restrito.')
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
        return HttpResponseForbidden('Acesso restrito.')
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
        return HttpResponseForbidden('Acesso restrito.')
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
