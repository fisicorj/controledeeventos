import json
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from activities.models import Activity, Session
from activities.views import can_manage
from registrations.models import Registration

from .models import Attendance

QR_PREFIX = 'ARENA-EVENTS:'


def activity_blocked(session):
    """Retorna a mensagem de bloqueio se a atividade não aceita mais check-in."""
    if session.activity.status != Activity.Status.PUBLISHED:
        return f'Check-in indisponível: a atividade está {session.activity.get_status_display().lower()}.'
    return None


def client_ip(request):
    # O X-Forwarded-For pode ser forjado pelo cliente. Só é usado quando há proxies confiáveis
    # (NUM_PROXIES), pegando o endereço que o proxy mais próximo de nós acrescentou.
    num_proxies = getattr(settings, 'NUM_PROXIES', 0)
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if num_proxies > 0 and forwarded:
        hops = [h.strip() for h in forwarded.split(',') if h.strip()]
        if len(hops) >= num_proxies:
            return hops[-num_proxies]
    return request.META.get('REMOTE_ADDR')


def get_managed_session(request, session_id):
    session = get_object_or_404(Session.objects.select_related('activity'), pk=session_id)
    return session, can_manage(request.user, session.activity)


@login_required
def session_attendance(request, session_id):
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        return HttpResponseForbidden('Acesso restrito.')
    registrations = (
        Registration.objects.filter(activity=session.activity, status=Registration.Status.CONFIRMED)
        .select_related('user')
        .order_by('user__first_name', 'user__username')
    )
    present = set(Attendance.objects.filter(session=session).values_list('registration_id', flat=True))
    return render(
        request,
        'attendance/session.html',
        {
            'session': session,
            'registrations': registrations,
            'present': present,
        },
    )


@login_required
def manual_checkin(request, session_id, registration_id):
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        return HttpResponseForbidden('Acesso restrito.')
    registration = get_object_or_404(
        Registration, pk=registration_id, activity=session.activity, status=Registration.Status.CONFIRMED
    )
    blocked = activity_blocked(session)
    if blocked:
        messages.error(request, blocked)
        return redirect('session_attendance', session_id=session.id)
    if request.method == 'POST':
        _, created = Attendance.objects.get_or_create(
            registration=registration,
            session=session,
            defaults={
                'validated_by': request.user,
                'method': Attendance.Method.MANUAL,
                'ip_address': client_ip(request),
            },
        )
        messages.success(request, 'Presença registrada.' if created else 'Presença já estava registrada.')
    return redirect('session_attendance', session_id=session.id)


@login_required
def qr_checkin(request, session_id):
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        return JsonResponse({'ok': False, 'message': 'Acesso restrito.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': 'Método inválido.'}, status=405)
    blocked = activity_blocked(session)
    if blocked:
        return JsonResponse({'ok': False, 'message': blocked}, status=400)
    if not session.check_in_open:
        return JsonResponse({'ok': False, 'message': 'Check-in desta sessão está fechado.'}, status=400)
    try:
        data = json.loads(request.body)
        token = uuid.UUID(str(data.get('token', '')).replace(QR_PREFIX, '').strip())
    except Exception:
        return JsonResponse({'ok': False, 'message': 'QR Code inválido ou de outra plataforma.'}, status=400)
    registration = (
        Registration.objects.filter(qr_token=token, activity=session.activity, status=Registration.Status.CONFIRMED)
        .select_related('user')
        .first()
    )
    if not registration:
        return JsonResponse({'ok': False, 'message': 'Inscrição não encontrada para esta atividade.'}, status=404)
    _, created = Attendance.objects.get_or_create(
        registration=registration,
        session=session,
        defaults={
            'validated_by': request.user,
            'method': Attendance.Method.QR_CODE,
            'ip_address': client_ip(request),
        },
    )
    return JsonResponse(
        {
            'ok': True,
            'created': created,
            'registration_id': registration.id,
            'present_count': Attendance.objects.filter(session=session).count(),
            'name': registration.user.get_full_name() or registration.user.username,
            'message': 'Presença registrada.' if created else 'Presença já registrada.',
        }
    )
