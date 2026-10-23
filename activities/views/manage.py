"""Gestão da atividade pela organização: painel, criar, editar, cancelar, finalizar, equipe de check-in."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from attendance.summary import participation
from certificates.models import Certificate
from certificates.roles import role_certificates
from certificates.services import finish_activity, issue_certificates
from core.pagination import paginate, semester_choices, semester_range
from core.permissions import activities_for, can_manage
from registrations import notifications, services, survey
from registrations import sessions as session_room
from registrations.models import Registration
from submissions.models import Submission

from ..forms import NOTIFY_FIELDS, ActivityForm
from ..models import Activity
from .content import categories_with_counts
from .public import with_counts, with_delivery


@login_required
def organizer_dashboard(request):
    if not can_manage(request.user):
        raise PermissionDenied('Acesso restrito a organizadores e administradores.')
    mine = activities_for(request.user, Activity.objects.all())
    qs = mine
    # Filtros: busca no título, semestre (pela data de início) e situação.
    query = request.GET.get('q', '').strip()
    semester = request.GET.get('semestre', '')
    status = request.GET.get('situacao', '')
    if query:
        qs = qs.filter(title__icontains=query)
    period = semester_range(semester)
    if period:
        qs = qs.filter(start_date__gte=period[0], start_date__lt=period[1])
    else:
        semester = ''
    if status in Activity.Status.values:
        qs = qs.filter(status=status)
    else:
        status = ''
    qs = with_counts(qs).annotate(registration_total=Count('registrations')).order_by('-start_date', '-id')
    page, querystring = paginate(request, qs, 20)
    context = {
        'activities': page.object_list,
        'page': page,
        'querystring': querystring,
        'query': query,
        'semester': semester,
        'status': status,
        'semesters': semester_choices(mine.datetimes('start_date', 'month')),
        'statuses': Activity.Status.choices,
        'filtered': bool(query or semester or status),
    }
    return render(request, 'organizer/dashboard.html', context)


@login_required
def activity_create(request):
    if not can_manage(request.user):
        raise PermissionDenied('Acesso restrito.')
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
        raise PermissionDenied('Acesso restrito.')
    old_status = activity.status
    before = {field: getattr(activity, field) for field in NOTIFY_FIELDS}
    form = ActivityForm(request.POST or None, request.FILES or None, instance=activity)
    if form.is_valid():
        with transaction.atomic():
            locked = services.lock_activity(activity.pk)
            if locked.status != old_status:
                # Outra pessoa finalizou ou cancelou enquanto este formulário estava aberto.
                messages.error(
                    request,
                    f'A atividade mudou para "{locked.get_status_display()}" enquanto você editava. '
                    'Confira e salve de novo.',
                )
                return redirect('activity_edit', pk=activity.pk)
            activity = form.save()
            new_status = activity.status
            if new_status == Activity.Status.CANCELLED and old_status != new_status:
                _cancel(activity)
            elif new_status == Activity.Status.FINISHED and old_status != new_status:
                issued = finish_activity(activity, request=request, check_status=False)
                messages.info(request, f'Atividade finalizada. {len(issued)} certificado(s) emitido(s).')
            else:
                _announce_change(request, activity, before, form.cleaned_data.get('notify_change'))
                # Se a capacidade aumentou, a fila é chamada na hora.
                promoted = services.promote_waitlist(activity)
                if promoted:
                    messages.info(request, f'{len(promoted)} pessoa(s) da lista de espera foram confirmadas.')
        messages.success(request, 'Atividade atualizada.')
        if getattr(form, 'sessions_filled', 0):
            messages.info(
                request,
                f'{form.sessions_filled} sessão(ões) sem vagas definidas ficaram com as vagas do evento '
                f'({activity.capacity}). Ajuste em cada sessão, se precisar.',
            )
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


def _announce_change(request, activity, before, notify):
    """Data ou local mudou: o lembrete de véspera volta a valer para a data nova e, se pedido, os inscritos
    recebem um e-mail com o antes e o depois."""
    changes = [(field, before[field], getattr(activity, field)) for field in NOTIFY_FIELDS
               if before[field] != getattr(activity, field)]  # fmt: skip
    if not changes:
        return
    active = activity.registrations.filter(
        status__in=[Registration.Status.CONFIRMED, Registration.Status.WAITLISTED]
    ).select_related('user', 'activity')
    if any(field == 'start_date' for field, _, _ in changes):
        active.update(reminder_sent_at=None)
    if notify:
        people = list(active)
        notifications.notify_activity_changed(activity, people, changes)
        if people:
            plural = 's' if len(people) != 1 else ''
            messages.info(request, f'{len(people)} inscrito{plural} avisado{plural} da mudança por e-mail.')


@login_required
def activity_delete(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if activity.registrations.exists() or Submission.objects.filter(call__activity=activity).exists():
        messages.error(
            request,
            'Esta atividade já possui inscrições ou trabalhos enviados e não pode ser excluída. '
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
        raise PermissionDenied('Acesso restrito.')
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
    """Efeitos do cancelamento: fecha o check-in, tira o prazo das vagas oferecidas e avisa inscritos e fila."""
    activity.sessions.update(check_in_open=False)
    services.clear_offers(activity)
    to_notify = activity.registrations.filter(
        status__in=[Registration.Status.CONFIRMED, Registration.Status.WAITLISTED]
    ).select_related('user', 'activity')
    notifications.notify_activity_cancelled(activity, to_notify)


@login_required
def activity_finish(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if activity.status != Activity.Status.PUBLISHED:
        messages.info(request, 'Só atividades publicadas podem ser finalizadas.')
        return redirect('activity_manage', pk=activity.pk)
    if request.method == 'POST':
        issued = finish_activity(activity, request=request)
        if issued is None:
            messages.info(request, 'Esta atividade já foi finalizada ou cancelada por outra pessoa.')
            return redirect('activity_manage', pk=activity.pk)
        messages.success(
            request,
            f'Atividade finalizada. {len(issued)} certificado(s) emitido(s); os avisos estão saindo por e-mail.'
            if activity.certificate_enabled
            else 'Atividade finalizada.',
        )
        return redirect('activity_manage', pk=activity.pk)
    sessions, rows = participation(activity)
    eligible = sum(1 for r in rows if r.percent >= activity.min_attendance_percent) if sessions else 0
    if activity.per_session:
        _per_session_rows(activity, sessions, rows)
        eligible = sum(row.attended for row in rows)
    from submissions.models import Submission as Work

    return render(
        request,
        'organizer/confirm_finish.html',
        {
            'activity': activity,
            'sessions': sessions,
            'total': len(rows),
            'eligible': eligible,
            'speakers': activity.speakers.count() if activity.speaker_certificate else 0,
            'works': Work.objects.filter(call__activity=activity, status=Work.Status.ACCEPTED).count()
            if activity.presenter_certificate
            else 0,
        },
    )


@login_required
def certificates_issue(request, pk):
    """Emite os certificados que ficaram pendentes (ex.: presença lançada depois de finalizar)."""
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method == 'POST' and activity.status == Activity.Status.FINISHED:
        with transaction.atomic():
            services.lock_activity(activity.pk)  # dois cliques ao mesmo tempo: o segundo espera e não duplica
            issued = issue_certificates(activity, request=request)
        messages.success(request, f'{len(issued)} certificado(s) novo(s) emitido(s).')
    return redirect('activity_manage', pk=activity.pk)


@login_required
def activity_manage(request, pk):
    activity = get_object_or_404(Activity.objects.prefetch_related('sessions').select_related('call_for_papers'), pk=pk)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    # Frequência calculada em consultas fixas (antes era uma consulta por inscrito).
    sessions, rows = participation(activity)
    certificates = {c.registration_id: c for c in Certificate.objects.filter(registration__activity=activity)}
    for row in rows:
        row.certificate = certificates.get(row.registration.id)
        row.certified = row.certificate is not None and not row.certificate.is_revoked
    certified_count = sum(1 for c in certificates.values() if not c.is_revoked)
    if activity.per_session:
        certified_count = _per_session_rows(activity, sessions, rows)
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
            'certified_count': certified_count,
            'session_list': session_room.with_room(activity, sessions),
            'role_certificates': role_certificates(activity) if activity.certificate_enabled else [],
            'without_sessions': _without_sessions(activity).count() if activity.per_session else 0,
            'speakers': activity.speakers.all(),
            'categories': categories_with_counts(activity),
            'materials': activity.materials.all(),
            'announcements': with_delivery(activity.announcements.select_related('sent_by')[:10]),
            'survey': survey.summary(activity) if activity.survey_enabled else None,
            'checkin_staff': activity.checkin_staff.order_by('first_name', 'username'),
            'survey_open': survey.survey_open(activity),
        },
    )


def _per_session_rows(activity, sessions, rows):
    """Atividade por sessão: em cada linha, as sessões escolhidas, as presenças nelas e os certificados.
    Devolve quantos certificados válidos há."""
    from registrations.models import SessionEnrollment

    chosen = {}
    for registration_id, session_id in SessionEnrollment.objects.filter(
        session__activity=activity, status=SessionEnrollment.Status.CONFIRMED
    ).values_list('registration_id', 'session_id'):
        chosen.setdefault(registration_id, set()).add(session_id)
    by_registration = {}
    for c in (
        Certificate.objects.filter(session_enrollment__session__activity=activity)
        .select_related('session_enrollment')
        .order_by('start_date')
    ):
        by_registration.setdefault(c.session_enrollment.registration_id, []).append(c)
    for row in rows:
        mine = chosen.get(row.registration.id, set())
        row.chosen = len(mine)
        row.attended = sum(1 for session, mark in zip(sessions, row.marks, strict=True) if mark and session.pk in mine)
        row.session_certificates = by_registration.get(row.registration.id, [])
    return sum(1 for items in by_registration.values() for c in items if not c.is_revoked)


def _without_sessions(activity):
    """Inscritos confirmados que ainda não escolheram nenhuma sessão (atividade por sessão)."""
    return activity.registrations.filter(status=Registration.Status.CONFIRMED, offer_expires_at__isnull=True).exclude(
        session_enrollments__status__in=session_room.ACTIVE
    )


@login_required
def sessions_remind(request, pk):
    """Avisa por e-mail quem está inscrito e ainda não escolheu sessões (sem sessão, não há certificado)."""
    activity = get_object_or_404(Activity, pk=pk)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method == 'POST' and activity.per_session and activity.status == Activity.Status.PUBLISHED:
        people = list(_without_sessions(activity).select_related('user', 'activity'))
        with transaction.atomic():
            notifications.notify_choose_sessions(people)
        messages.success(request, f'{len(people)} inscrito(s) avisado(s) por e-mail para escolher as sessões.')
    return redirect('activity_manage', pk=activity.pk)


@login_required
def checkin_staff_add(request, activity_id):
    """Inclui um monitor na equipe de check-in, pelo usuário de acesso ou pelo e-mail."""
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method == 'POST':
        typed = request.POST.get('person', '').strip()
        person = (
            User.objects.filter(is_active=True)
            .filter(Q(username__iexact=typed) | Q(email__iexact=typed, email_verified=True))
            .first()
            if typed
            else None
        )
        if person is None:
            messages.error(
                request,
                f'Não encontrei uma conta com o usuário ou e-mail "{typed}". A pessoa precisa ter conta no portal.',
            )
        elif can_manage(person, activity):
            messages.info(request, f'{person} já gerencia esta atividade e pode fazer check-in.')
        else:
            activity.checkin_staff.add(person)
            messages.success(request, f'{person} entrou na equipe de check-in desta atividade.')
    return redirect('activity_manage', pk=activity.pk)


@login_required
def checkin_staff_remove(request, activity_id, user_id):
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method == 'POST':
        person = activity.checkin_staff.filter(pk=user_id).first()
        if person:
            activity.checkin_staff.remove(person)
            messages.success(request, f'{person} saiu da equipe de check-in.')
    return redirect('activity_manage', pk=activity.pk)
