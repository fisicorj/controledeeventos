"""Relatório da sessão (M-07): presença e notas da pesquisa de uma sessão, para a organização ver e enviar aos
palestrantes dela por e-mail. O palestrante recebe os números, os nomes de quem teve presença e os comentários da
pesquisa sem identificação (nunca e-mails nem documentos dos participantes)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from attendance.models import Attendance
from core.permissions import can_manage
from registrations.models import Registration
from registrations.sessions import session_registrations
from registrations.survey import session_summary

from .models import Session


def report_data(session):
    expected = session_registrations(session).filter(status=Registration.Status.CONFIRMED).count()
    present = list(
        Attendance.objects.filter(session=session)
        .select_related('registration__user')
        .order_by('registration__user__first_name', 'registration__user__last_name', 'registration__user__username')
    )
    names = [a.registration.user.get_full_name() or a.registration.user.username for a in present]
    return {
        'session': session,
        'activity': session.activity,
        'speakers': list(session.speakers.all()),
        'expected': expected,
        'present_names': names,
        'rate': round(len(names) * 100 / expected) if expected else None,
        'survey': session_summary(session),
    }


def _managed_session(request, session_id):
    session = get_object_or_404(Session.objects.select_related('activity'), pk=session_id)
    if not can_manage(request.user, session.activity):
        raise PermissionDenied('Acesso restrito.')
    return session


@login_required
def session_report(request, session_id):
    session = _managed_session(request, session_id)
    return render(request, 'organizer/session_report.html', report_data(session))


@login_required
@require_POST
def session_report_send(request, session_id):
    from registrations.notifications import queue

    session = _managed_session(request, session_id)
    data = report_data(session)
    speakers = [s for s in data['speakers'] if s.email]
    for speaker in speakers:
        queue(
            speaker.email,
            f'relatório da sessão "{session.title}"',
            'session_report',
            {**data, 'name': speaker.name.split(' ')[0]},
            group=f'session:{session.pk}:report',
        )
    if speakers:
        messages.success(
            request, f'Relatório enviado a {len(speakers)} palestrante(s): ' + ', '.join(s.name for s in speakers) + '.'
        )
    else:
        messages.error(request, 'Nenhum palestrante desta sessão tem e-mail cadastrado.')
    return redirect('session_report', session_id=session.pk)
