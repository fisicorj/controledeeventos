import json
import logging
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from activities.models import Activity, Session
from core.http import client_ip
from core.middleware import allow_camera
from core.permissions import can_checkin, can_manage, is_platform_admin
from registrations.models import Registration
from registrations.qr import QR_PREFIX
from registrations.sessions import session_registrations

from .audit import record_removals
from .models import Attendance, AttendanceRemoval

logger = logging.getLogger(__name__)


def activity_blocked(session, allow_finished=False):
    """Retorna a mensagem de bloqueio se a atividade não aceita check-in.

    Em atividade finalizada a equipe ainda pode lançar presença esquecida (manual ou leitura
    feita offline antes de finalizar); depois, "Emitir certificados pendentes" completa os certificados."""
    status = session.activity.status
    if status == Activity.Status.PUBLISHED or (allow_finished and status == Activity.Status.FINISHED):
        return None
    return f'Check-in indisponível: a atividade está {session.activity.get_status_display().lower()}.'


OFFLINE_BEFORE, OFFLINE_AFTER = timedelta(hours=2), timedelta(hours=6)


def offline_reading_time(session, scanned_at):
    """Leitura feita no celular sem internet e enviada depois: devolve a hora da leitura se ela aconteceu perto
    do horário da sessão (e não no futuro); senão, None."""
    from django.utils.dateparse import parse_datetime

    try:
        when = parse_datetime(str(scanned_at or ''))
    except ValueError:
        return None
    if when is None:
        return None
    if timezone.is_naive(when):
        when = timezone.make_aware(when)
    ok = (
        session.start_at - OFFLINE_BEFORE <= when <= session.end_at + OFFLINE_AFTER
        and when <= timezone.now() + timedelta(minutes=5)
    )
    return min(when, timezone.now()) if ok else None


def get_managed_session(request, session_id):
    """Sessão e se o usuário pode fazer check-in nela (organização ou equipe de check-in da atividade)."""
    session = get_object_or_404(Session.objects.select_related('activity'), pk=session_id)
    return session, can_checkin(request.user, session.activity)


@login_required
@allow_camera
def session_attendance(request, session_id):
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    # Na atividade por sessão, só quem escolheu esta sessão (e está confirmado nela).
    registrations = session_registrations(session).select_related('user').order_by('user__first_name', 'user__username')
    present = set(Attendance.objects.filter(session=session).values_list('registration_id', flat=True))
    return render(
        request,
        'attendance/session.html',
        {
            'session': session,
            'registrations': registrations,
            'present': present,
            'removals': AttendanceRemoval.objects.filter(session_id=session.pk)[:50],
            'waitlist': _session_waitlist(session),
            # A equipe de check-in não vê e-mails nem os atalhos da gestão.
            'can_manage_activity': can_manage(request.user, session.activity),
        },
    )


def _session_waitlist(session):
    """Fila da sessão (atividade por sessão), só para mostrar: quem está nela não pode ter presença ainda."""
    if not session.activity.per_session:
        return []
    from registrations.models import SessionEnrollment

    return (
        SessionEnrollment.objects.filter(session=session, status=SessionEnrollment.Status.WAITLISTED)
        .select_related('registration__user')
        .order_by('waitlisted_at', 'id')
    )


@login_required
@require_POST
def walk_in_checkin(request, session_id):
    """Inscrição na hora (M-06): quem chegou sem inscrição, pela conta do portal (usuário ou e-mail confirmado)."""
    from django.db.models import Q

    from accounts.models import User
    from registrations import staff

    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    back = redirect('session_attendance', session_id=session.id)
    typed = request.POST.get('person', '').strip()
    person = (
        User.objects.filter(Q(username__iexact=typed) | Q(email__iexact=typed, email_verified=True)).first()
        if typed
        else None
    )
    if person is None:
        messages.error(
            request,
            f'Não encontrei uma conta com o usuário ou e-mail "{typed}". A pessoa cria a conta pelo celular (e '
            'confirma o e-mail) e você tenta de novo.',
        )
        return back
    activity = session.activity
    category = activity.categories.filter(pk=request.POST.get('category') or 0).first()
    if activity.categories.exists() and category is None:
        messages.error(request, 'Escolha o tipo de vaga da pessoa.')
        return back
    try:
        _, over = staff.walk_in(session, person, request.user, category, can_exceed=can_manage(request.user, activity))
    except staff.Refused as exc:
        messages.error(request, str(exc))
        return back
    text = f'{person.get_full_name() or person.username} foi inscrito e a presença foi registrada.'
    if over:
        text += ' A inscrição ficou acima das vagas.'
    messages.success(request, text)
    return back


@login_required
def manual_checkin(request, session_id, registration_id):
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    registration = get_object_or_404(session_registrations(session), pk=registration_id)
    blocked = activity_blocked(session, allow_finished=True)
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
@require_POST
def remove_checkin(request, session_id, registration_id):
    """Desfaz uma presença registrada por engano (ex.: clique na linha errada do check-in manual)."""
    from certificates.models import Certificate

    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    registration = get_object_or_404(
        Registration.objects.select_related('user'), pk=registration_id, activity=session.activity
    )
    blocked = activity_blocked(session, allow_finished=True)
    if blocked:
        messages.error(request, blocked)
        return redirect('session_attendance', session_id=session.id)
    with transaction.atomic():
        attendances = list(
            Attendance.objects.select_for_update()
            .filter(session=session, registration=registration)
            .select_related('session__activity', 'registration__user', 'validated_by')
        )
        if attendances:
            record_removals(attendances, actor=request.user, ip=client_ip(request))
            Attendance.objects.filter(pk__in=[a.pk for a in attendances]).delete()
    deleted = len(attendances)
    name = registration.user.get_full_name() or registration.user.username
    if not deleted:
        messages.info(request, f'A presença de {name} já não estava registrada.')
        return redirect('session_attendance', session_id=session.id)
    messages.success(request, f'Presença de {name} removida desta sessão.')
    if session.activity.per_session:
        certificate = Certificate.objects.filter(
            session_enrollment__registration=registration, session_enrollment__session=session, revoked_at__isnull=True
        ).first()
        if certificate:
            messages.warning(
                request,
                f'{name} já tem o certificado desta sessão ({certificate.code}). '
                'Se ele não vale mais, revogue-o em "Gerenciar".',
            )
        return redirect('session_attendance', session_id=session.id)
    certificate = Certificate.objects.filter(registration=registration, revoked_at__isnull=True).first()
    if certificate:
        activity = session.activity
        total = activity.sessions.count()
        present = Attendance.objects.filter(registration=registration).count()
        percent = round(present * 100 / total) if total else 0
        if percent < activity.min_attendance_percent:
            messages.warning(
                request,
                f'{name} já tem certificado ({certificate.code}) e a frequência agora é {percent}%, '
                f'abaixo do mínimo de {activity.min_attendance_percent}%. '
                'Se o certificado não vale mais, revogue-o em "Gerenciar".',
            )
    return redirect('session_attendance', session_id=session.id)


@login_required
@require_POST
def toggle_checkin(request, session_id):
    """Abre ou fecha o check-in da sessão (a equipe de check-in também pode, sem editar a sessão)."""
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    blocked = activity_blocked(session)
    if blocked:
        messages.error(request, blocked)
    else:
        session.check_in_open = not session.check_in_open
        session.save(update_fields=['check_in_open'])
        messages.success(request, 'Check-in aberto.' if session.check_in_open else 'Check-in fechado.')
    return redirect('session_attendance', session_id=session.id)


@login_required
def qr_checkin(request, session_id):
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        return JsonResponse({'ok': False, 'message': 'Acesso restrito.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'message': 'Método inválido.'}, status=405)
    try:
        data = json.loads(request.body)
        token = uuid.UUID(str(data.get('token', '')).replace(QR_PREFIX, '').strip())
    except Exception:
        return JsonResponse({'ok': False, 'message': 'QR Code inválido ou de outra plataforma.'}, status=400)
    # Leitura guardada no celular offline: aceita mesmo que o check-in já tenha sido fechado ou a atividade
    # finalizada depois, desde que tenha sido feita no horário da sessão.
    scanned_at = offline_reading_time(session, data.get('scanned_at')) if data.get('offline') else None
    late_sync = scanned_at is not None
    blocked = activity_blocked(session, allow_finished=late_sync)
    if blocked:
        return JsonResponse({'ok': False, 'message': blocked}, status=400)
    if not session.check_in_open and not late_sync:
        return JsonResponse({'ok': False, 'message': 'Check-in desta sessão está fechado.'}, status=400)
    registration = session_registrations(session).filter(qr_token=token).select_related('user').first()
    if not registration:
        if session.activity.per_session and session.activity.registrations.filter(qr_token=token).exists():
            return JsonResponse(
                {'ok': False, 'message': 'Inscrito no evento, mas não está confirmado nesta sessão.'}, status=404
            )
        return JsonResponse({'ok': False, 'message': 'Inscrição não encontrada para esta atividade.'}, status=404)
    _, created = Attendance.objects.get_or_create(
        registration=registration,
        session=session,
        defaults={
            'validated_by': request.user,
            'method': Attendance.Method.QR_CODE,
            'ip_address': client_ip(request),
            'check_in': scanned_at or timezone.now(),
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


# ---------- Check-in como aplicativo (PWA): funciona com internet ruim e sincroniza depois


def _token_key(token):
    """Os 12 primeiros caracteres do token identificam a pessoa no celular, sem guardar o token inteiro."""
    return str(token).replace('-', '')[:12]


NOW_BEFORE, NOW_AFTER = timedelta(hours=1), timedelta(hours=1)  # janela de "acontecendo agora"


def _checkin_sessions(request):
    """Sessões que a pessoa pode controlar, das últimas 12 horas em diante (organização, equipe ou admin)."""
    from django.db.models import Q

    is_team = request.user.checkin_activities.exists()
    if not (can_manage(request.user) or is_team):
        raise PermissionDenied('Acesso restrito à organização e à equipe de check-in.')
    now = timezone.now()
    sessions = Session.objects.filter(
        activity__status=Activity.Status.PUBLISHED, end_at__gte=now - timedelta(hours=12)
    ).select_related('activity')
    if not is_platform_admin(request.user):
        sessions = sessions.filter(Q(activity__organizer=request.user) | Q(activity__checkin_staff=request.user))
    return now, sessions.distinct()


def _is_now(session, now):
    return session.start_at - NOW_BEFORE <= now <= session.end_at + NOW_AFTER


@login_required
def checkin_quick(request):
    """Atalho do celular ("Ler QR Code"): com uma sessão acontecendo agora, abre o leitor dela direto;
    com várias (ou nenhuma), abre a lista para escolher."""
    now, sessions = _checkin_sessions(request)
    current = [s for s in sessions.filter(start_at__lte=now + NOW_BEFORE, end_at__gte=now - NOW_AFTER)]
    if len(current) == 1:
        return redirect('checkin_app', session_id=current[0].pk)
    return redirect('checkin_home')


@login_required
def checkin_home(request):
    """Tela inicial do app: sessões que o usuário pode controlar; as que estão acontecendo agora vêm primeiro."""
    now, sessions = _checkin_sessions(request)
    sessions = list(sessions.order_by('start_at')[:30])
    for session in sessions:
        session.is_now = _is_now(session, now)
    sessions.sort(key=lambda s: (not s.is_now, s.start_at))
    return render(
        request,
        'attendance/app_home.html',
        {'sessions': sessions, 'now': now, 'is_organizer': can_manage(request.user)},
    )


@login_required
@allow_camera
def checkin_app(request, session_id):
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    return render(request, 'attendance/app.html', {'session': session, 'blocked': activity_blocked(session)})


@login_required
def checkin_roster(request, session_id):
    """Lista de inscritos para o celular conferir o QR mesmo sem internet."""
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        return JsonResponse({'ok': False, 'message': 'Acesso restrito.'}, status=403)
    present = set(Attendance.objects.filter(session=session).values_list('registration_id', flat=True))
    people = [
        {
            'id': r.id,
            'k': _token_key(r.qr_token),
            'name': r.user.get_full_name() or r.user.username,
            'present': r.id in present,
        }
        for r in session_registrations(session).select_related('user').order_by('user__first_name', 'user__username')
    ]
    return JsonResponse(
        {
            'ok': True,
            'open': session.check_in_open and not activity_blocked(session),
            'present_count': len(present),
            'people': people,
        }
    )


@login_required
def checkin_status(request, session_id):
    """Contador ao vivo (consultado a cada poucos segundos pelo app)."""
    session, allowed = get_managed_session(request, session_id)
    if not allowed:
        return JsonResponse({'ok': False, 'message': 'Acesso restrito.'}, status=403)
    present = list(Attendance.objects.filter(session=session).values_list('registration_id', flat=True))
    return JsonResponse({'ok': True, 'present_count': len(present), 'present': present})


def checkin_manifest(request):
    from django.templatetags.static import static
    from django.urls import reverse

    from core.models import SiteSettings

    site = SiteSettings.load()
    data = {
        'name': f'Check-in {site.portal_name}',
        'short_name': 'Check-in',
        'start_url': reverse('checkin_quick'),
        # Atalho no ícone do app (toque longo, Android): abre o leitor da sessão que está acontecendo agora.
        'shortcuts': [{'name': 'Ler QR Code', 'short_name': 'Ler QR', 'url': reverse('checkin_quick')}],
        'scope': '/attendance/',
        'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#0b1220',
        'theme_color': site.safe_primary_color,
        'icons': [
            {'src': static('pwa/icon-192.png'), 'sizes': '192x192', 'type': 'image/png'},
            {'src': static('pwa/icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
        ],
    }
    return JsonResponse(data, content_type='application/manifest+json')


def checkin_service_worker(request):
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    response = HttpResponse(
        render_to_string('attendance/sw.js', request=request), content_type='application/javascript'
    )
    response['Service-Worker-Allowed'] = '/attendance/'
    response['Cache-Control'] = 'no-cache'
    return response
