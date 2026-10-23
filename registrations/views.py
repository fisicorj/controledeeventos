from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from activities.models import Activity
from activities.operations import materials_for
from activities.views import activity_detail

from . import notifications, services, sessions
from .forms import RegistrationAnswersForm, RegistrationOptionsForm
from .models import Registration, SessionEnrollment, SurveyResponse
from .qr import qr_png


@login_required
def register_activity(request, activity_id):
    if request.method != 'POST':
        return redirect('activity_detail', pk=activity_id)
    activity = get_object_or_404(Activity, pk=activity_id, status=Activity.Status.PUBLISHED)
    if not request.user.email_verified:
        messages.warning(request, 'Confirme o seu e-mail antes de se inscrever.')
        return redirect('verify_email')

    # As respostas são validadas antes de travar a atividade; com erro, a página volta com o formulário preenchido.
    form = RegistrationAnswersForm(activity.questions.all(), request.POST)
    options = RegistrationOptionsForm(activity, request.user, request.POST)
    if not (form.is_valid() & options.is_valid()):
        messages.error(request, 'Confira os dados do formulário de inscrição.')
        return activity_detail(request, activity.pk, answers_form=form, options_form=options)
    category = options.cleaned_data.get('category')

    with transaction.atomic():
        activity = services.lock_activity(activity.pk)
        if not _registration_open(activity):
            messages.error(request, 'As inscrições não estão abertas para esta atividade.')
            return redirect('activity_detail', pk=activity.id)
        services.promote_waitlist(activity)
        registration = Registration.objects.filter(user=request.user, activity=activity).first()
        full = not services.has_room(activity, category)
        if refused := _refuse_registration(request, activity, registration, category, full):
            return refused
        registration = _save_registration(request.user, activity, registration, category, full, form)
        chosen, refused = [], []
        if activity.per_session and not full:
            chosen, refused = sessions.choose(registration, options.cleaned_data.get('sessions') or [])
        _announce_registration(request, registration, full)
        if activity.per_session:
            _announce_sessions(request, registration, full, chosen, refused)
    return redirect('my_registrations')


def _announce_sessions(request, registration, full, chosen, refused):
    if full:
        messages.info(request, 'Quando a sua vaga no evento for confirmada, escolha as sessões em "Minhas inscrições".')
        return
    summary = sessions.describe(chosen, refused)
    if summary:
        (messages.warning if refused else messages.info)(request, summary)
    if not chosen:
        messages.warning(request, 'Você ainda não tem nenhuma sessão. Escolha em "Minhas inscrições".')


def _registration_open(activity):
    now = timezone.now()
    in_period = activity.registration_start <= now <= activity.registration_end
    return activity.status == Activity.Status.PUBLISHED and in_period


def _refuse_registration(request, activity, registration, category, full):
    """Motivo para não inscrever (já inscrito, sem vaga e sem fila): devolve o redirecionamento com o aviso;
    None se pode seguir."""
    if registration and registration.status == Registration.Status.CONFIRMED:
        messages.info(request, 'Você já está inscrito nesta atividade.')
        return redirect('my_registrations')
    if registration and registration.status == Registration.Status.WAITLISTED:
        messages.info(request, f'Você já está na lista de espera (posição {registration.waitlist_position()}).')
        return redirect('my_registrations')
    if full and not activity.waitlist_enabled:
        text = f'Não há mais vagas do tipo "{category.name}".' if category else 'Não há vagas disponíveis.'
        messages.error(request, text)
        return redirect('activity_detail', pk=activity.id)
    return None


def _save_registration(user, activity, registration, category, full, form):
    """Cria a inscrição (ou reaproveita a cancelada), confirmada ou na lista de espera, e grava as respostas."""
    if registration is None:
        registration = Registration(user=user, activity=activity)
    registration.status = Registration.Status.WAITLISTED if full else Registration.Status.CONFIRMED
    registration.category = category
    registration.cancelled_at = None
    registration.waitlisted_at = timezone.now() if full else None
    registration.reminder_sent_at = None
    registration.save()
    form.save(registration)
    return registration


def _announce_registration(request, registration, full):
    if full:
        notifications.notify_waitlisted(registration)
        messages.info(
            request,
            f'As vagas estão esgotadas. Você entrou na lista de espera na posição '
            f'{registration.waitlist_position()} e será avisado por e-mail se uma vaga abrir.',
        )
    else:
        notifications.notify_registration_confirmed(registration)
        messages.success(request, 'Inscrição confirmada. Enviamos o QR Code para o seu e-mail.')


CANCEL_AFTER_START = (
    'A atividade já começou: a inscrição não pode mais ser cancelada (a presença e o certificado dependem dela). '
    'Se precisar, fale com a organização.'
)


@login_required
def cancel_registration(request, pk):
    registration = get_object_or_404(Registration.objects.select_related('activity'), pk=pk, user=request.user)
    if request.method != 'POST':
        if registration.status == Registration.Status.CANCELLED:
            return redirect('my_registrations')
        if registration.status == Registration.Status.CONFIRMED and registration.activity.start_date <= timezone.now():
            messages.error(request, CANCEL_AFTER_START)
            return redirect('my_registrations')
        # Confirmação antes de cancelar: a vaga passa na hora para o próximo da fila.
        waiting = registration.activity.registrations.filter(status=Registration.Status.WAITLISTED).exists()
        return render(
            request, 'registrations/confirm_cancel.html', {'registration': registration, 'has_waitlist': waiting}
        )
    with transaction.atomic():
        activity = services.lock_activity(registration.activity_id)
        registration.refresh_from_db()
        if registration.status == Registration.Status.CANCELLED:
            messages.info(request, 'Esta inscrição já estava cancelada.')
            return redirect('my_registrations')
        if activity.status != Activity.Status.PUBLISHED:
            messages.error(request, 'Não é possível alterar a inscrição de uma atividade finalizada ou cancelada.')
            return redirect('my_registrations')
        if registration.status == Registration.Status.CONFIRMED and activity.start_date <= timezone.now():
            messages.error(request, CANCEL_AFTER_START)
            return redirect('my_registrations')
        was_waitlisted = registration.status == Registration.Status.WAITLISTED
        registration.status = Registration.Status.CANCELLED
        registration.waitlisted_at = None
        registration.cancelled_at = timezone.now()
        registration.save(update_fields=['status', 'waitlisted_at', 'cancelled_at'])
        notifications.notify_registration_cancelled(registration, was_waitlisted=was_waitlisted)
        sessions.cancel_all(registration)  # as vagas das sessões escolhidas vão para as filas delas
        if not was_waitlisted:
            # A vaga liberada vai para o primeiro da fila.
            services.promote_waitlist(activity)
    messages.success(request, 'Você saiu da lista de espera.' if was_waitlisted else 'Inscrição cancelada.')
    return redirect('my_registrations')


@login_required
def confirm_offer(request, pk):
    """Quem saiu da lista de espera confirma a vaga reservada."""
    registration = get_object_or_404(Registration.objects.select_related('activity'), pk=pk, user=request.user)
    if request.method == 'POST':
        if services.confirm_offer(registration):
            messages.success(request, 'Vaga confirmada! Enviamos o QR Code para o seu e-mail.')
        else:
            messages.error(
                request, 'O prazo para confirmar esta vaga terminou e ela passou para a próxima pessoa da fila.'
            )
    return redirect('my_registrations')


@login_required
def my_registrations(request):
    registrations = list(
        Registration.objects.filter(user=request.user)
        .select_related('activity', 'certificate')
        .prefetch_related('activity__materials')
        .annotate(
            attendance_count=Count('attendances', distinct=True),
            session_count=Count('activity__sessions', distinct=True),
        )
        .order_by('-created_at')
    )
    # Posição na fila de espera: uma consulta para todas as atividades (DES-03), em vez de uma por inscrição.
    waiting = {r.activity_id for r in registrations if r.status == Registration.Status.WAITLISTED}
    queue = {}
    for activity_id, waitlisted_at in Registration.objects.filter(
        activity_id__in=waiting, status=Registration.Status.WAITLISTED, waitlisted_at__isnull=False
    ).values_list('activity_id', 'waitlisted_at'):
        queue.setdefault(activity_id, []).append(waitlisted_at)
    answered = set(
        SurveyResponse.objects.filter(registration__in=[r.pk for r in registrations]).values_list(
            'registration_id', flat=True
        )
    )
    for registration in registrations:
        activity = registration.activity
        registration.can_survey = (
            activity.survey_enabled
            and activity.status == Activity.Status.FINISHED
            and registration.status == Registration.Status.CONFIRMED
            and (registration.session_count == 0 or registration.attendance_count > 0)
            and registration.pk not in answered
        )
        registration.survey_blocks_certificate = registration.can_survey and activity.survey_required
        attended = registration.session_count == 0 or registration.attendance_count > 0
        registration.materials = (
            materials_for(request.user, activity, registration=registration, attended=attended)
            if registration.status == Registration.Status.CONFIRMED and activity.materials.all()
            else []
        )
        registration.position = (
            sum(1 for moment in queue.get(activity.pk, ()) if moment < registration.waitlisted_at) + 1
            if registration.status == Registration.Status.WAITLISTED and registration.waitlisted_at
            else None
        )
        registration.started = activity.start_date <= timezone.now()
        registration.attendance_percent = (
            round(registration.attendance_count * 100 / registration.session_count)
            if registration.session_count
            else None
        )
    _add_session_choices(registrations)
    return render(request, 'registrations/my_registrations.html', {'registrations': registrations})


def _add_session_choices(registrations):
    """Atividade por sessão: sessões escolhidas e certificados de cada inscrição, em duas consultas."""
    from certificates.models import Certificate

    per_session = {r.pk: r for r in registrations if r.activity.per_session}
    for registration in per_session.values():
        registration.chosen_sessions, registration.session_certificates = [], []
    if not per_session:
        return
    for enrollment in (
        SessionEnrollment.objects.filter(registration_id__in=per_session, status__in=sessions.ACTIVE)
        .select_related('session')
        .order_by('session__start_at')
    ):
        per_session[enrollment.registration_id].chosen_sessions.append(enrollment)
    for certificate in Certificate.objects.filter(
        session_enrollment__registration_id__in=per_session, revoked_at__isnull=True
    ).select_related('session_enrollment'):
        per_session[certificate.session_enrollment.registration_id].session_certificates.append(certificate)


@login_required
def registration_sessions(request, pk):
    """Atividade com inscrição por sessão: escolher, trocar e sair das sessões até cada uma começar."""
    registration = get_object_or_404(
        Registration.objects.select_related('activity'), pk=pk, user=request.user, activity__per_session=True
    )
    activity = registration.activity
    if request.method == 'POST':
        _change_session(request, registration)
        return redirect('registration_sessions', pk=registration.pk)
    mine = {
        e.session_id: e
        for e in registration.session_enrollments.select_related('certificate').exclude(
            status=SessionEnrollment.Status.CANCELLED, certificate__isnull=True
        )
    }
    rows = sessions.with_room(activity, activity.sessions.prefetch_related('speakers'))
    for session in rows:
        session.mine = mine.get(session.pk)
        if session.mine and session.mine.status == SessionEnrollment.Status.WAITLISTED:
            session.position = session.mine.waitlist_position()
    return render(
        request,
        'registrations/sessions.html',
        {
            'registration': registration,
            'activity': activity,
            'sessions': rows,
            'can_choose': sessions.can_choose(registration),
            'survey_blocks': _survey_blocks(registration),
        },
    )


def _survey_blocks(registration):
    from .survey import needs_survey

    return registration.activity.status == Activity.Status.FINISHED and needs_survey(registration)


def _change_session(request, registration):
    session = registration.activity.sessions.filter(pk=request.POST.get('session')).first()
    if session is None:
        messages.error(request, 'Sessão não encontrada.')
        return
    if request.POST.get('action') == 'confirm':
        enrollment = registration.session_enrollments.filter(session=session).first()
        if enrollment and sessions.confirm_offer(enrollment):
            messages.success(request, f'Vaga confirmada em "{session.title}".')
        else:
            messages.error(request, 'O prazo para confirmar esta vaga terminou e ela passou para a próxima pessoa.')
        return
    try:
        if request.POST.get('action') == 'leave':
            enrollment = registration.session_enrollments.filter(session=session).first()
            if enrollment and sessions.leave(enrollment):
                messages.success(request, f'Você saiu de "{session.title}".')
            return
        enrollment = sessions.enroll(registration, session)
    except sessions.Refused as exc:
        messages.error(request, str(exc))
        return
    if enrollment.status == SessionEnrollment.Status.WAITLISTED:
        messages.info(
            request,
            f'"{session.title}" está lotada: você entrou na lista de espera, na posição '
            f'{enrollment.waitlist_position()}. Se abrir uma vaga, a sua entra automaticamente e avisamos por e-mail.',
        )
    else:
        messages.success(request, f'Você está confirmado em "{session.title}".')


@login_required
def my_calendar(request, pk):
    """Agenda pessoal (.ics): no modo por sessão, só as sessões em que a pessoa está confirmada."""
    registration = get_object_or_404(
        Registration.objects.select_related('activity'),
        pk=pk,
        user=request.user,
        status=Registration.Status.CONFIRMED,
        activity__status=Activity.Status.PUBLISHED,
    )
    name, content = notifications.personal_ics(registration)
    response = HttpResponse(content, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{name}"'
    return response


@login_required
def qr_code(request, pk):
    registration = get_object_or_404(
        Registration,
        pk=pk,
        user=request.user,
        status=Registration.Status.CONFIRMED,
        offer_expires_at__isnull=True,  # vaga oferecida ainda não confirmada: sem QR
        activity__status=Activity.Status.PUBLISHED,
    )
    return HttpResponse(qr_png(registration), content_type='image/png')
