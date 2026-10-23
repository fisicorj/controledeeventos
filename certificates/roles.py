"""Certificados de palestrante e de apresentação de trabalho.

Saem junto com os de participação, ao finalizar a atividade (e no "Emitir certificados pendentes"), quando a
atividade emite certificados e a opção de cada tipo está ligada:
- palestrante: um por palestrante cadastrado, com as sessões em que ele fala e a soma das horas delas (sem sessões
  ligadas, vale o período e a carga horária da atividade). O PDF vai por e-mail para o endereço do palestrante,
  quando informado; com uma conta confirmada nesse e-mail, aparece também em "Minhas horas";
- apresentação: um por autor de cada trabalho aprovado na chamada, citando todos os autores: o autor de contato
  e cada coautor com e-mail informado na submissão. O coautor recebe o PDF no e-mail informado e, com uma conta
  confirmada nesse e-mail, vê o certificado em "Minhas horas". Com presença exigida, o coautor precisa estar
  inscrito no evento com esse e-mail e ter presença na sessão do trabalho.
"""

from django.db.models import Q

from . import audit
from .models import Certificate

Role = Certificate.Role


def _join(items):
    items = list(items)
    if len(items) <= 1:
        return ''.join(items)
    return ', '.join(items[:-1]) + ' e ' + items[-1]


def issue_role_certificates(activity, request=None):
    """Emite os certificados de palestrante e de apresentação que faltam. Devolve os novos."""
    if not activity.certificate_enabled:
        return []
    issued = []
    if activity.speaker_certificate:
        issued += _speakers(activity)
    if activity.presenter_certificate:
        issued += _presenters(activity)
    if issued:
        audit.record(audit.Action.ISSUED, issued, request=request, details=lambda c: {'source': c.get_role_display()})
        _notify(issued)
    return issued


def _speakers(activity):
    issued = []
    for speaker in activity.speakers.filter(certificate__isnull=True).prefetch_related('sessions'):
        sessions = sorted(speaker.sessions.all(), key=lambda s: s.start_at)
        if sessions:
            start, end = sessions[0].start_at, sessions[-1].end_at
            hours = sum(s.certificate_hours for s in sessions)
        else:
            start, end, hours = activity.start_date, activity.end_date, activity.workload_hours
        issued.append(
            Certificate.objects.create(
                speaker=speaker,
                role=Role.SPEAKER,
                role_detail=_join(f'“{s.title}”' for s in sessions)[:600],
                participant_name=speaker.name,
                participant_document=speaker.document,
                participant_email=speaker.email,
                activity_title=activity.title,
                activity_type=activity.get_activity_type_display(),
                start_date=start,
                end_date=end,
                workload_hours=hours,
            )
        )
    return issued


def _presenters(activity):
    from accounts.models import User
    from submissions.models import Submission

    accepted = (
        Submission.objects.filter(call__activity=activity, status=Submission.Status.ACCEPTED)
        .select_related('author', 'session')
        .prefetch_related('certificates')
    )
    present = users_by_email = None
    if activity.presenter_requires_attendance:
        from attendance.models import Attendance

        present = set(
            Attendance.objects.filter(session__activity=activity).values_list('registration__user_id', 'session_id')
        )
    issued = []
    for work in accepted:
        done = {c.coauthor_email for c in work.certificates.all()}
        author = work.author
        people = []  # (coauthor_email, nome, documento, e-mail, conta)
        if '' not in done:
            name = author.get_full_name() or author.username
            people.append(('', name, author.institutional_id, author.email, author.pk))
        coauthors = [(name, email) for name, email in work.coauthor_entries() if email and email not in done]
        if coauthors and users_by_email is None:
            users_by_email = _verified_users_by_email(User, activity)
        for coauthor_name, email in coauthors:
            account = users_by_email.get(email)
            document = account.institutional_id if account else ''
            people.append((email, coauthor_name, document, email, account.pk if account else None))
        session = work.session
        authors = _join([author.get_full_name() or author.username, *work.coauthor_list()])[:600]
        for coauthor_email, name, document, email, user_id in people:
            if present is not None and not _presented(work, user_id, present):
                continue
            issued.append(
                Certificate.objects.create(
                    submission=work,
                    coauthor_email=coauthor_email,
                    role=Role.PRESENTER,
                    role_detail=authors,
                    participant_name=name[:300],
                    participant_document=document,
                    participant_email=email,
                    activity_title=work.title[:200],
                    event_title=activity.title,
                    activity_type=activity.get_activity_type_display(),
                    start_date=session.start_at if session else activity.start_date,
                    end_date=session.end_at if session else activity.end_date,
                    workload_hours=session.certificate_hours if session else None,
                )
            )
    return issued


def _verified_users_by_email(user_model, activity):
    """Contas com e-mail confirmado inscritas no evento, pelo e-mail (para a presença dos coautores)."""
    users = user_model.objects.filter(registrations__activity=activity, email_verified=True).distinct()
    return {u.email.lower(): u for u in users if u.email}


def _presented(work, user_id, present):
    """A pessoa teve presença na sessão do trabalho (sem sessão definida: em qualquer sessão do evento).
    Coautor sem conta inscrita no evento não tem como ter presença registrada."""
    if user_id is None:
        return False
    if work.session_id:
        return (user_id, work.session_id) in present
    return any(uid == user_id for uid, _ in present)


def _notify(certificates):
    from django.urls import reverse

    from registrations.notifications import absolute_url, queue

    for c in certificates:
        if not c.participant_email:
            continue
        first = c.participant_name.split(' ')[0]
        queue(
            c.participant_email,
            f'seu certificado de {"palestrante" if c.role == Role.SPEAKER else "apresentação de trabalho"}: '
            f'{c.event_title or c.activity_title}',
            'role_certificate_issued',
            {
                'name': first,
                'certificate': c,
                'verify_url': absolute_url(reverse('certificate_verify_code', args=[c.code])),
                'my_hours_url': absolute_url(reverse('my_hours')),
            },
            [{'kind': 'certificate_pdf', 'id': c.pk}],
            group=f'activity:{c.source_activity.pk}:certificates',
        )


def role_certificates(activity):
    """Certificados de palestrante e de apresentação da atividade, para a tela de gestão."""
    return (
        Certificate.objects.filter(Q(speaker__activity=activity) | Q(submission__call__activity=activity))
        .select_related('speaker', 'submission')
        .order_by('role', 'participant_name')
    )
