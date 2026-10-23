"""Inscrições vistas pela organização: incluir participante, cancelar e mexer nas sessões de uma pessoa."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from core.permissions import can_manage
from registrations import sessions as session_room
from registrations import staff
from registrations.models import Registration, SessionEnrollment

from ..models import Activity

NOTE_MAX = 1000


def _note(request):
    return (request.POST.get('note') or '').strip()[:NOTE_MAX]


@login_required
def participant_add(request, activity_id):
    """Inclui uma pessoa pelo usuário de acesso ou pelo e-mail confirmado (quem chegou na hora, convidado)."""
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method != 'POST':
        return redirect('activity_manage', pk=activity.pk)
    typed = request.POST.get('person', '').strip()
    person = (
        User.objects.filter(Q(username__iexact=typed) | Q(email__iexact=typed, email_verified=True)).first()
        if typed
        else None
    )
    if person is None:
        messages.error(
            request,
            f'Não encontrei uma conta com o usuário ou e-mail "{typed}". A pessoa precisa criar a conta no portal '
            '(e confirmar o e-mail) antes.',
        )
        return redirect('activity_manage', pk=activity.pk)
    category = activity.categories.filter(pk=request.POST.get('category') or 0).first()
    if activity.categories.exists() and category is None:
        messages.error(request, 'Escolha o tipo de vaga da pessoa.')
        return redirect('activity_manage', pk=activity.pk)
    try:
        registration, over = staff.add_participant(activity, person, request.user, category, _note(request))
    except staff.Refused as exc:
        messages.error(request, str(exc))
        return redirect('activity_manage', pk=activity.pk)
    text = f'{person.get_full_name() or person.username} foi inscrito e recebeu o QR Code por e-mail.'
    if over:
        text += ' A inscrição ficou acima das vagas.'
    messages.success(request, text)
    return redirect('registration_admin', pk=registration.pk)


@login_required
def participant_batch(request, activity_id):
    """Inscrição em lote (O-02): cola a lista ou envia um CSV, confere o que vai acontecer e confirma."""
    from registrations import batch
    from registrations.services import confirmed_count

    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if activity.status != Activity.Status.PUBLISHED:
        messages.error(request, 'Só dá para incluir participantes numa atividade publicada.')
        return redirect('activity_manage', pk=activity.pk)
    categories = list(activity.categories.all())
    context = {'activity': activity, 'categories': categories, 'max_rows': batch.MAX_ROWS}
    if request.method != 'POST':
        return render(request, 'organizer/participant_batch.html', context)

    text = request.POST.get('people', '')
    category = activity.categories.filter(pk=request.POST.get('category') or 0).first()
    context.update(people=text, category=category, note=_note(request))
    error = None
    if categories and category is None:
        error = 'Escolha o tipo de vaga das pessoas.'
    try:
        if request.FILES.get('file'):
            text = '\n'.join([text, batch.read_file(request.FILES['file'])])
        typed = batch.parse(text)
    except (ValueError, batch.TooMany) as exc:
        error, typed = str(exc), []
    if not error and not typed:
        error = 'Cole a lista ou envie um arquivo CSV com os usuários ou e-mails.'
    if error:
        context['error'] = error
        return render(request, 'organizer/participant_batch.html', context)

    rows = batch.resolve(activity, typed)
    to_add = [row for row in rows if row.status == batch.ADD]
    if request.POST.get('confirm') != '1':
        free = max(activity.capacity - confirmed_count(activity), 0)
        context.update(
            rows=rows,
            to_add=to_add,
            add_list='\n'.join(row.typed for row in to_add),
            over=max(len(to_add) - free, 0),
            free=free,
        )
        return render(request, 'organizer/participant_batch.html', context)

    added, refused = 0, []
    for row in to_add:
        try:
            staff.add_participant(activity, row.user, request.user, category, context['note'])
            added += 1
        except staff.Refused as exc:
            refused.append(f'{row.typed}: {exc}')
    if added:
        messages.success(request, f'{added} pessoa(s) inscrita(s); cada uma recebeu o QR Code por e-mail.')
    if refused:
        messages.warning(request, 'Não inscritos: ' + ' | '.join(refused[:10]) + (' ...' if len(refused) > 10 else ''))
    if not added and not refused:
        messages.info(request, 'Ninguém para inscrever.')
    return redirect('activity_manage', pk=activity.pk)


@login_required
def registration_admin(request, pk):
    """A inscrição de uma pessoa, para a organização: situação, sessões e histórico das ações."""
    registration = get_object_or_404(Registration.objects.select_related('activity', 'user', 'category'), pk=pk)
    activity = registration.activity
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method == 'POST':
        _act(request, registration)
        return redirect('registration_admin', pk=registration.pk)
    mine = {e.session_id: e for e in registration.session_enrollments.all()}
    rows = session_room.with_room(activity, activity.sessions.all()) if activity.per_session else []
    for session in rows:
        session.mine = mine.get(session.pk)
    return render(
        request,
        'organizer/registration_admin.html',
        {
            'registration': registration,
            'activity': activity,
            'sessions': rows,
            'logs': registration.logs.select_related('actor'),
            'has_certificates': staff.has_certificates(registration),
        },
    )


def _act(request, registration):
    action = request.POST.get('action')
    note = _note(request)
    try:
        if action == 'cancel':
            if not note:
                raise staff.Refused('Informe o motivo do cancelamento: ele vai no e-mail à pessoa.')
            staff.cancel(registration, request.user, note)
            messages.success(request, 'Inscrição cancelada e pessoa avisada por e-mail.')
        elif action in ('add_session', 'remove_session'):
            session = registration.activity.sessions.filter(pk=request.POST.get('session') or 0).first()
            if session is None:
                raise staff.Refused('Sessão não encontrada.')
            if action == 'add_session':
                _, over = staff.add_to_session(registration, session, request.user, note)
                text = f'Pessoa confirmada em "{session.title}" e avisada por e-mail.'
                messages.success(request, text + (' A sessão ficou acima das vagas.' if over else ''))
            else:
                enrollment = SessionEnrollment.objects.filter(registration=registration, session=session).first()
                if enrollment is None:
                    raise staff.Refused('A pessoa não estava nesta sessão.')
                staff.remove_from_session(enrollment, request.user, note)
                messages.success(request, f'Pessoa tirada de "{session.title}" e avisada por e-mail.')
    except staff.Refused as exc:
        messages.error(request, str(exc))
