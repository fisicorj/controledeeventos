"""Comunicados da organização aos inscritos de uma atividade."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from core.permissions import can_manage
from registrations import notifications
from registrations.models import Registration

from .forms import AnnouncementForm
from .models import Activity, Announcement


def recipients(activity, audience):
    statuses = Announcement(audience=audience).statuses()
    return activity.registrations.filter(status__in=statuses).select_related('user', 'activity')


@login_required
def announcement_create(request, activity_id):
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    form = AnnouncementForm(request.POST or None)
    if form.is_valid():
        announcement = form.save(commit=False)
        announcement.activity, announcement.sent_by = activity, request.user
        targets = list(recipients(activity, announcement.audience))
        if not targets:
            form.add_error('audience', 'Não há ninguém neste grupo para receber o comunicado.')
        else:
            announcement.recipients_count = len(targets)
            announcement.save()
            queued = notifications.send_announcement(announcement, targets)
            plural = 's' if queued != 1 else ''
            messages.success(
                request,
                f'Comunicado na fila de envio para {queued} pessoa{plural}. Os e-mails saem em instantes; '
                'o andamento aparece na lista de comunicados.',
            )
            return redirect('activity_manage', pk=activity.pk)
    counts = {value: recipients(activity, value).count() for value, _ in Announcement.Audience.choices}
    return render(
        request,
        'organizer/announcement_form.html',
        {'form': form, 'activity': activity, 'counts': counts, 'confirmed': Registration.Status.CONFIRMED},
    )
