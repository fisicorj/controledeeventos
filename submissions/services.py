"""Regras da chamada de trabalhos: designar avaliadores, receber avaliações, decidir e inscrever o autor."""

from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from activities.models import Activity
from certificates.models import Certificate
from registrations import services as registration_services
from registrations.models import Registration

from . import notifications
from .models import Review, ReviewDecline, ReviewerAreas, Submission, SubmissionEvent

Status = Submission.Status


def received(submission):
    """Trabalho novo: confirma ao autor por e-mail."""
    notifications.notify_received(submission)


def resubmitted(submission):
    """O autor mandou a versão corrigida: nova versão e as avaliações reabrem para os mesmos avaliadores."""
    submission.version += 1
    reviews = list(submission.reviews.select_related('reviewer'))
    submission.status = Status.UNDER_REVIEW if reviews else Status.SUBMITTED
    submission.save(update_fields=['version', 'status', 'updated_at'])
    for review in reviews:
        review.submitted_at = None
        review.due_at = due_date(submission.call)
        review.reminders_sent = 0
        review.reminded_at = None
        review.save(update_fields=['submitted_at', 'due_at', 'reminders_sent', 'reminded_at'])
        notifications.notify_reviewer(review, again=True)
    notifications.notify_received(submission, again=True)


def can_manage_call(user, call):
    """Cuidar da chamada: a organização do evento (ou um administrador) e a coordenação da chamada."""
    from core.permissions import can_manage

    if can_manage(user, call.activity):
        return True
    return user.is_authenticated and call.coordinators.filter(pk=user.pk).exists()


def can_review(user, submission):
    """Avaliador ainda na lista da chamada, designado para o trabalho, e não é o próprio autor."""
    return (
        user.is_authenticated
        and submission.author_id != user.id
        and submission.reviews.filter(reviewer=user).exists()
        and submission.call.reviewers.filter(pk=user.pk).exists()
    )


def _plain(text):
    import unicodedata

    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    return ' '.join(text.lower().split())


def possible_conflicts(submission, reviewers):
    """Avaliadores cujo nome completo ou e-mail aparece na lista de coautores (os coautores são texto livre)."""
    coauthors = _plain(submission.coauthors)
    emails = {email for _, email in submission.coauthor_entries() if email}
    found = []
    for person in reviewers:
        name = _plain(person.get_full_name())
        if name and len(name.split()) > 1 and name in coauthors:
            found.append(person)
        elif person.email and person.email.lower() in emails:
            found.append(person)
    return found


@transaction.atomic
def assign(submission, reviewers):
    """Deixa designados exatamente estes avaliadores. Quem já entregou a avaliação não é tirado.
    Devolve (incluídos, retirados)."""
    reviewers = [r for r in reviewers if r.pk != submission.author_id]
    current = {review.reviewer_id: review for review in submission.reviews.all()}
    wanted = {r.pk for r in reviewers}
    added = []
    for reviewer in reviewers:
        if reviewer.pk not in current:
            review = Review.objects.create(
                submission=submission, reviewer=reviewer, version=submission.version, due_at=due_date(submission.call)
            )
            added.append(review)
            notifications.notify_reviewer(review)
    removed = [r for pk, r in current.items() if pk not in wanted and not r.is_done]
    Review.objects.filter(pk__in=[r.pk for r in removed]).delete()
    if submission.status == Status.SUBMITTED and submission.reviews.exists():
        submission.status = Status.UNDER_REVIEW
        submission.save(update_fields=['status', 'updated_at'])
    return added, removed


def submit_review(review):
    review.submitted_at = timezone.now()
    review.version = review.submission.version
    review.save()
    submission = review.submission
    if not submission.reviews.filter(submitted_at__isnull=True).exists():
        notifications.notify_reviews_done(submission)


class Refused(Exception):
    """A operação não pode ser feita; a mensagem explica por quê."""


@transaction.atomic
def decide(submission, decision, note, user):
    """Decisão da organização. Aprovado com o evento publicado: o autor fica inscrito (mesmo lotado: quem apresenta
    tem vaga). Com o evento finalizado, a decisão vale, mas não há inscrição a fazer. Evento cancelado: recusa."""
    submission = Submission.objects.select_for_update().select_related('call__activity', 'author').get(pk=submission.pk)
    activity = submission.call.activity
    if activity.status == Activity.Status.CANCELLED:
        raise Refused('O evento foi cancelado: os trabalhos não recebem mais decisão.')
    if submission.is_final:
        raise Refused('Este trabalho já tem decisão. Para mudar, reabra a avaliação.')
    before = submission.status
    submission.status = decision
    submission.decision_note = note.strip()
    submission.decided_at = timezone.now()
    submission.decided_by = user
    submission.save()
    SubmissionEvent.objects.create(
        submission=submission, action=SubmissionEvent.Action.DECIDED, status_before=before,
        status_after=decision, note=submission.decision_note, actor=user,
    )  # fmt: skip
    registration = None
    if decision == Status.ACCEPTED and activity.status == Activity.Status.PUBLISHED:
        registration = register_author(submission)
    notifications.notify_decision(submission, registration)
    return submission, registration


REOPENABLE = (Status.ACCEPTED, Status.REJECTED)


@transaction.atomic
def reopen(submission, note, user):
    """Desfaz uma decisão (clique errado, recurso do autor): o trabalho volta para a avaliação e o autor é avisado.
    A inscrição feita na aprovação continua (a pessoa pode estar contando com ela); a organização decide depois."""
    submission = Submission.objects.select_for_update().select_related('call__activity', 'author').get(pk=submission.pk)
    if submission.status not in REOPENABLE:
        raise Refused('Só trabalhos aprovados ou não aprovados podem ter a avaliação reaberta.')
    if Certificate.objects.filter(submission=submission).exists():
        raise Refused('O trabalho já tem certificado de apresentação: a decisão não pode mais ser reaberta.')
    before = submission.status
    submission.status = Status.UNDER_REVIEW if submission.reviews.exists() else Status.SUBMITTED
    submission.save(update_fields=['status', 'updated_at'])
    SubmissionEvent.objects.create(
        submission=submission, action=SubmissionEvent.Action.REOPENED, status_before=before,
        status_after=submission.status, note=note.strip(), actor=user,
    )  # fmt: skip
    notifications.notify_reopened(submission, note.strip())
    return submission


def register_author(submission):
    """Inscreve (ou confirma) o autor no evento, sem passar pela fila nem pelo limite de vagas."""
    activity = registration_services.lock_activity(submission.call.activity_id)
    registration = Registration.objects.filter(user=submission.author, activity=activity).first()
    if registration is None:
        registration = Registration(user=submission.author, activity=activity)
    registration.status = Registration.Status.CONFIRMED
    registration.waitlisted_at = None
    registration.cancelled_at = None
    registration.offer_expires_at = None
    registration.save()
    if submission.session_id:
        _enroll_presenter(registration, submission.session)
    return registration


def set_session(submission, session):
    """Coloca o trabalho aprovado numa sessão. Na atividade por sessão, o autor fica confirmado nela."""
    with transaction.atomic():
        submission.session = session
        submission.save(update_fields=['session', 'updated_at'])
        if session and submission.status == Status.ACCEPTED:
            registration_services.lock_activity(submission.call.activity_id)
            registration = Registration.objects.filter(
                user=submission.author, activity_id=submission.call.activity_id, status=Registration.Status.CONFIRMED
            ).first()
            if registration:
                _enroll_presenter(registration, session)


def _enroll_presenter(registration, session):
    if not registration.activity.per_session:
        return
    from registrations.models import SessionEnrollment

    enrollment, _ = SessionEnrollment.objects.get_or_create(registration=registration, session=session)
    if enrollment.status != SessionEnrollment.Status.CONFIRMED:
        enrollment.status = SessionEnrollment.Status.CONFIRMED  # quem apresenta não fica na fila da sessão
        enrollment.waitlisted_at = None
        enrollment.cancelled_at = None
        enrollment.save()


@transaction.atomic
def withdraw(submission):
    submission = Submission.objects.select_for_update().get(pk=submission.pk)
    if submission.is_final:
        return False
    submission.status = Status.WITHDRAWN
    submission.save(update_fields=['status', 'updated_at'])
    submission.reviews.filter(submitted_at__isnull=True).delete()
    return True


# ---------- Prazo e lembretes dos avaliadores (M-03)

REMIND_BEFORE = timedelta(days=2)  # primeiro lembrete: 2 dias antes do prazo
REMIND_EVERY = timedelta(days=3)  # depois do prazo: a cada 3 dias
MAX_REMINDERS = 3
OPEN_FOR_REVIEW = (Status.SUBMITTED, Status.UNDER_REVIEW)


def due_date(call, now=None):
    """Prazo da avaliação: designação + review_days, sem passar do início do evento (V7-05)."""
    now = now or timezone.now()
    deadline = now + timedelta(days=call.review_days)
    start = call.activity.start_date
    return min(deadline, start) if start > now else deadline


def pending_reviews(call=None):
    """Avaliações designadas e ainda não entregues, de trabalhos esperando parecer num evento publicado."""
    reviews = Review.objects.filter(
        submitted_at__isnull=True,
        submission__status__in=OPEN_FOR_REVIEW,
        submission__call__activity__status=Activity.Status.PUBLISHED,
    )
    if call is not None:
        reviews = reviews.filter(submission__call=call)
    return reviews.select_related('reviewer', 'submission__call__activity')


def _send_reminders(reviews, now):
    by_reviewer = defaultdict(list)
    for review in reviews:
        by_reviewer[review.reviewer].append(review)
    for reviewer, items in by_reviewer.items():
        notifications.notify_review_reminder(reviewer, sorted(items, key=lambda r: r.due_at or now), now)
        Review.objects.filter(pk__in=[r.pk for r in items]).update(
            reminded_at=now, reminders_sent=F('reminders_sent') + 1
        )
    return len(by_reviewer)


def remind_reviewers(now=None):
    """Lembrete automático (worker, de hora em hora): avaliações pendentes perto do prazo ou atrasadas. Um e-mail
    por avaliador, com todos os trabalhos pendentes dele. Devolve quantos avaliadores foram lembrados."""
    now = now or timezone.now()
    due = [
        review
        for review in pending_reviews().filter(due_at__lte=now + REMIND_BEFORE, reminders_sent__lt=MAX_REMINDERS)
        if review.reminded_at is None or review.reminded_at <= now - REMIND_EVERY
    ]
    with transaction.atomic():
        return _send_reminders(due, now)


def remind_reviewer_now(call, reviewer):
    """Lembrete pedido pela organização: todas as avaliações pendentes da pessoa nesta chamada."""
    reviews = list(pending_reviews(call).filter(reviewer=reviewer))
    if reviews:
        _send_reminders(reviews, timezone.now())
    return len(reviews)


# ---------- Distribuição automática (M-04)


def reviewer_areas(call):
    """{id do avaliador: [áreas]} (lista vazia: aceita todas)."""
    return {row.reviewer_id: row.area_list() for row in ReviewerAreas.objects.filter(call=call)}


def set_reviewer_areas(call, reviewer, areas):
    valid = [a for a in call.area_list() if a in set(areas)]
    ReviewerAreas.objects.update_or_create(call=call, reviewer=reviewer, defaults={'areas': '\n'.join(valid)})
    return valid


@transaction.atomic
def auto_assign(call, per_submission):
    """Completa cada trabalho esperando avaliação até `per_submission` avaliadores: só quem aceita a área do
    trabalho, nunca o autor nem quem aparece entre os coautores, e sempre o avaliador com menos trabalhos nesta
    chamada primeiro. Quem já está designado continua. Devolve (avaliações criadas, trabalhos que ficaram sem
    avaliadores suficientes)."""
    from django.db.models import Count, Q

    reviewers = list(
        call.reviewers.filter(is_active=True).annotate(load=Count('reviews', filter=Q(reviews__submission__call=call)))
    )
    areas = reviewer_areas(call)
    declined = defaultdict(set)  # quem recusou um trabalho não é escolhido de novo para ele
    for submission_id, reviewer_id in ReviewDecline.objects.filter(submission__call=call).values_list(
        'submission_id', 'reviewer_id'
    ):
        declined[submission_id].add(reviewer_id)
    load = {r.pk: r.load for r in reviewers}
    created, short = 0, []
    works = (
        call.submissions.filter(status__in=OPEN_FOR_REVIEW)
        .select_related('author')
        .prefetch_related('reviews')
        .order_by('created_at')
    )
    for work in works:
        current = [r.reviewer for r in work.reviews.all()]
        missing = per_submission - len(current)
        if missing <= 0:
            continue
        taken = {r.pk for r in current} | declined.get(work.pk, set())
        conflicts = {p.pk for p in possible_conflicts(work, reviewers)}
        candidates = [
            r
            for r in reviewers
            if r.pk not in taken
            and r.pk != work.author_id
            and r.pk not in conflicts
            and (not work.area or not areas.get(r.pk) or work.area in areas[r.pk])
        ]
        candidates.sort(key=lambda r: (load[r.pk], (r.get_full_name() or r.username).lower()))
        chosen = candidates[:missing]
        if chosen:
            added, _ = assign(work, current + chosen)
            created += len(added)
            for reviewer in chosen:
                load[reviewer.pk] += 1
        if len(chosen) < missing:
            short.append(work)
    return created, short


# ---------- Recusa da designação (R5)


@transaction.atomic
def decline_review(review, reason, note=''):
    """O avaliador não vai avaliar: a avaliação pendente sai, fica o registro e a organização é avisada.
    Avaliação já entregue não pode ser recusada (a organização pode retirá-la, se for o caso)."""
    review = (
        Review.objects.select_for_update().select_related('submission__call__activity', 'reviewer').get(pk=review.pk)
    )
    if review.is_done:
        raise Refused('Esta avaliação já foi entregue.')
    submission = review.submission
    decline = ReviewDecline.objects.create(
        submission=submission, reviewer=review.reviewer, reason=reason, note=note.strip()[:1000]
    )
    review.delete()
    notifications.notify_review_declined(decline)
    remaining = submission.reviews.all()
    if remaining.exists() and not remaining.filter(submitted_at__isnull=True).exists():
        notifications.notify_reviews_done(submission)
    return decline


# ---------- Reaproveitar avaliadores (R3)


def calls_for(user):
    """Chamadas que a pessoa cuida (organização ou coordenação), da mais nova para a mais antiga."""
    from django.db.models import Q

    from core.permissions import is_platform_admin

    from .models import CallForPapers

    calls = CallForPapers.objects.select_related('activity')
    if not is_platform_admin(user):
        calls = calls.filter(Q(activity__organizer=user) | Q(coordinators=user)).distinct()
    return calls.order_by('-activity__start_date')


@transaction.atomic
def copy_reviewers(source, target):
    """Traz para a chamada os avaliadores de outra, com as áreas que existirem nas duas. Quem já está fica.
    Devolve (avaliadores incluídos, avaliadores cujas áreas não existem nesta chamada)."""
    target_areas = set(target.area_list())
    source_areas = reviewer_areas(source)
    current = set(target.reviewers.values_list('pk', flat=True))
    added, unmatched = [], []
    for person in source.reviewers.filter(is_active=True):
        if person.pk in current:
            continue
        target.reviewers.add(person)
        added.append(person)
        wanted = source_areas.get(person.pk, [])
        if wanted:
            kept = [a for a in wanted if a in target_areas]
            if kept:
                set_reviewer_areas(target, person, kept)
            else:
                unmatched.append(person)  # sem área em comum: fica com todas, e a tela avisa
    return added, unmatched


def review_menu(user):
    """Item "Avaliações" do menu (R1): aparece para quem é avaliador ou coordena alguma chamada, com o número de
    avaliações pendentes. Calculado a partir dos dados, sem papel próprio na conta."""
    from .models import CallForPapers

    if not user.is_authenticated:
        return {'show': False, 'pending': 0}
    pending = pending_reviews().filter(reviewer=user).count()
    show = (
        pending
        or Review.objects.filter(reviewer=user).exists()
        or CallForPapers.objects.filter(reviewers=user).exists()
        or CallForPapers.objects.filter(coordinators=user).exists()
    )
    return {'show': bool(show), 'pending': pending}
