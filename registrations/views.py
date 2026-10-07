from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from activities.models import Activity
from activities.views import activity_detail

from . import notifications, services
from .forms import RegistrationAnswersForm
from .models import Registration
from .qr import qr_png


@login_required
def register_activity(request, activity_id):
    if request.method != 'POST':
        return redirect('activity_detail', pk=activity_id)
    activity = get_object_or_404(Activity, pk=activity_id, status=Activity.Status.PUBLISHED)

    # As respostas são validadas antes de travar a atividade; com erro, a página volta com o formulário preenchido.
    form = RegistrationAnswersForm(activity.questions.all(), request.POST)
    if not form.is_valid():
        messages.error(request, 'Confira as respostas do formulário de inscrição.')
        return activity_detail(request, activity.pk, answers_form=form)

    with transaction.atomic():
        activity = services.lock_activity(activity.pk)
        now = timezone.now()
        if activity.status != Activity.Status.PUBLISHED or not (
            activity.registration_start <= now <= activity.registration_end
        ):
            messages.error(request, 'As inscrições não estão abertas para esta atividade.')
            return redirect('activity_detail', pk=activity.id)

        services.promote_waitlist(activity)
        registration = Registration.objects.filter(user=request.user, activity=activity).first()
        if registration and registration.status == Registration.Status.CONFIRMED:
            messages.info(request, 'Você já está inscrito nesta atividade.')
            return redirect('my_registrations')
        if registration and registration.status == Registration.Status.WAITLISTED:
            messages.info(request, f'Você já está na lista de espera (posição {registration.waitlist_position()}).')
            return redirect('my_registrations')

        full = services.confirmed_count(activity) >= activity.capacity
        if full and not activity.waitlist_enabled:
            messages.error(request, 'Não há vagas disponíveis para esta atividade.')
            return redirect('activity_detail', pk=activity.id)

        status = Registration.Status.WAITLISTED if full else Registration.Status.CONFIRMED
        if registration is None:
            registration = Registration(user=request.user, activity=activity)
        registration.status = status
        registration.waitlisted_at = now if full else None
        registration.reminder_sent_at = None
        registration.save()
        form.save(registration)

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
    return redirect('my_registrations')


@login_required
def cancel_registration(request, pk):
    registration = get_object_or_404(Registration, pk=pk, user=request.user)
    if request.method != 'POST':
        return redirect('my_registrations')
    with transaction.atomic():
        activity = services.lock_activity(registration.activity_id)
        registration.refresh_from_db()
        if registration.status == Registration.Status.CANCELLED:
            messages.info(request, 'Esta inscrição já estava cancelada.')
            return redirect('my_registrations')
        if activity.status != Activity.Status.PUBLISHED:
            messages.error(request, 'Não é possível alterar a inscrição de uma atividade finalizada ou cancelada.')
            return redirect('my_registrations')
        was_waitlisted = registration.status == Registration.Status.WAITLISTED
        registration.status = Registration.Status.CANCELLED
        registration.waitlisted_at = None
        registration.save(update_fields=['status', 'waitlisted_at'])
        notifications.notify_registration_cancelled(registration, was_waitlisted=was_waitlisted)
        if not was_waitlisted:
            # A vaga liberada vai para o primeiro da fila.
            services.promote_waitlist(activity)
    messages.success(request, 'Você saiu da lista de espera.' if was_waitlisted else 'Inscrição cancelada.')
    return redirect('my_registrations')


@login_required
def my_registrations(request):
    registrations = list(
        Registration.objects.filter(user=request.user)
        .select_related('activity', 'certificate')
        .annotate(
            attendance_count=Count('attendances', distinct=True),
            session_count=Count('activity__sessions', distinct=True),
        )
        .order_by('-created_at')
    )
    for registration in registrations:
        registration.position = registration.waitlist_position()
        registration.attendance_percent = (
            round(registration.attendance_count * 100 / registration.session_count)
            if registration.session_count
            else None
        )
    return render(request, 'registrations/my_registrations.html', {'registrations': registrations})


@login_required
def qr_code(request, pk):
    registration = get_object_or_404(
        Registration,
        pk=pk,
        user=request.user,
        status=Registration.Status.CONFIRMED,
        activity__status=Activity.Status.PUBLISHED,
    )
    return HttpResponse(qr_png(registration), content_type='image/png')
