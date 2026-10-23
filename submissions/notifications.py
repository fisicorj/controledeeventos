"""E-mails da chamada de trabalhos (pela fila de e-mails, como os demais avisos)."""

from django.urls import reverse
from django.utils import timezone

from registrations.notifications import _ticket, absolute_url, queue


def _context(submission):
    activity = submission.call.activity
    return {
        'submission': submission,
        'activity': activity,
        'name': submission.author.first_name or submission.author.username,
        'activity_url': absolute_url(activity.get_absolute_url()),
        'submission_url': absolute_url(reverse('submission_detail', args=[submission.pk])),
    }


def notify_received(submission, again=False):
    queue(
        submission.author.email,
        f'{"nova versão recebida" if again else "trabalho recebido"}: {submission.title}',
        'submission_received',
        {**_context(submission), 'again': again},
        group=f'submission:{submission.pk}',
    )


def notify_reviewer(review, again=False):
    submission = review.submission
    reviewer = review.reviewer
    queue(
        reviewer.email,
        f'{"nova versão para avaliar" if again else "trabalho para avaliar"} em {submission.call.activity.title}',
        'review_assigned',
        {
            **_context(submission),
            'name': reviewer.first_name or reviewer.username,
            'again': again,
            'review': review,
            'review_url': absolute_url(reverse('review_form', args=[review.pk])),
        },
        group=f'submission:{submission.pk}:reviews',
    )


def notify_review_reminder(reviewer, reviews, now=None):
    """Lembrete ao avaliador: avaliações pendentes, com o prazo de cada uma."""
    now = now or timezone.now()
    overdue = any(r.due_at and r.due_at < now for r in reviews)
    queue(
        reviewer.email,
        'avaliação atrasada' if overdue else 'lembrete: prazo de avaliação',
        'review_reminder',
        {
            'name': reviewer.first_name or reviewer.username,
            'items': [
                {
                    'review': r,
                    'overdue': bool(r.due_at and r.due_at < now),
                    'url': absolute_url(reverse('review_form', args=[r.pk])),
                }
                for r in reviews
            ],
            'reviews_url': absolute_url(reverse('my_reviews')),
        },
        group=f'reviewer:{reviewer.pk}:reminders',
    )


def call_managers(call):
    """Quem cuida da chamada: a organização do evento e a coordenação da chamada (sem repetir pessoas)."""
    people = [call.activity.organizer, *call.coordinators.filter(is_active=True)]
    seen, unique = set(), []
    for person in people:
        if person.pk not in seen and person.email:
            seen.add(person.pk)
            unique.append(person)
    return unique


def notify_reviews_done(submission):
    """Todas as avaliações entregues: avisa a organização e a coordenação para decidir."""
    for person in call_managers(submission.call):
        queue(
            person.email,
            f'avaliações concluídas: {submission.title}',
            'reviews_done',
            {
                **_context(submission),
                'name': person.first_name or person.username,
                'manage_url': absolute_url(reverse('submission_manage', args=[submission.pk])),
            },
            group=f'submission:{submission.pk}:reviews',
        )


def notify_review_declined(decline):
    """O avaliador recusou: a organização e a coordenação designam outra pessoa."""
    submission = decline.submission
    for person in call_managers(submission.call):
        queue(
            person.email,
            f'avaliador recusou: {submission.title}',
            'review_declined',
            {
                **_context(submission),
                'name': person.first_name or person.username,
                'decline': decline,
                'reviewer_name': decline.reviewer.get_full_name() or decline.reviewer.username,
                'manage_url': absolute_url(reverse('submission_manage', args=[submission.pk])),
            },
            group=f'submission:{submission.pk}:reviews',
        )


def notify_invite_accepted(invite):
    """Convite de avaliador aceito: quem cuida da chamada vê com que conta (pode não ser a do e-mail convidado)."""
    call, user = invite.call, invite.accepted_user
    for person in call_managers(call):
        queue(
            person.email,
            f'convite de avaliador aceito: {call.activity.title}',
            'reviewer_invite_accepted',
            {
                'name': person.first_name or person.username,
                'activity': call.activity,
                'invite': invite,
                'reviewer_name': user.get_full_name() or user.username,
                'reviewer_email': user.email,
                'other_email': (user.email or '').lower() != invite.email.lower(),
                'manage_url': absolute_url(reverse('call_manage', args=[call.activity_id])),
            },
            group=f'call:{call.pk}:team',
        )


def notify_coordinator_added(call, person, actor):
    queue(
        person.email,
        f'coordenação da chamada de trabalhos: {call.activity.title}',
        'call_coordinator_added',
        {
            'name': person.first_name or person.username,
            'activity': call.activity,
            'inviter': actor.get_full_name() or actor.username,
            'manage_url': absolute_url(reverse('call_manage', args=[call.activity_id])),
        },
        group=f'call:{call.pk}:team',
    )


def notify_reviewer_added(call, person, actor):
    queue(
        person.email,
        f'você foi incluído como avaliador em {call.activity.title}',
        'reviewer_added',
        {
            'name': person.first_name or person.username,
            'activity': call.activity,
            'inviter': actor.get_full_name() or actor.username,
            'reviews_url': absolute_url(reverse('my_reviews')),
        },
        group=f'call:{call.pk}:team',
    )


def notify_decision(submission, registration=None):
    comments = [r.comments_to_author for r in submission.reviews.all() if r.is_done and r.comments_to_author.strip()]
    queue(
        submission.author.email,
        f'resultado da avaliação: {submission.title}',
        'submission_decision',
        {
            **_context(submission),
            'comments': comments,
            'registration': registration,
        },
        _ticket(registration) if registration else (),
        group=f'submission:{submission.pk}',
    )


def notify_reopened(submission, note):
    queue(
        submission.author.email,
        f'avaliação reaberta: {submission.title}',
        'submission_reopened',
        {**_context(submission), 'note': note},
        group=f'submission:{submission.pk}',
    )
