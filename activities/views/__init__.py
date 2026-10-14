"""Views das atividades, divididas por assunto (ARQ-02). As rotas continuam as mesmas (activities/urls.py)."""

from .content import (
    category_create,
    category_delete,
    category_edit,
    question_create,
    question_delete,
    question_edit,
    session_create,
    session_delete,
    session_edit,
    speaker_create,
    speaker_delete,
    speaker_edit,
)
from .manage import (
    activity_cancel,
    activity_create,
    activity_delete,
    activity_edit,
    activity_finish,
    activity_manage,
    certificates_issue,
    checkin_staff_add,
    checkin_staff_remove,
    organizer_dashboard,
)
from .public import (
    activity_detail,
    activity_list,
    activity_queryset,
    calendar_ics,
    event_page,
    home,
    registration_state,
    upcoming_queryset,
    with_counts,
)

__all__ = [
    'activity_cancel', 'activity_create', 'activity_delete', 'activity_detail', 'activity_edit', 'activity_finish',
    'activity_list', 'activity_manage', 'activity_queryset', 'calendar_ics', 'category_create', 'category_delete',
    'category_edit', 'certificates_issue', 'checkin_staff_add', 'checkin_staff_remove', 'event_page', 'home',
    'organizer_dashboard', 'question_create', 'question_delete', 'question_edit', 'registration_state',
    'session_create', 'session_delete', 'session_edit', 'speaker_create', 'speaker_delete', 'speaker_edit',
    'upcoming_queryset', 'with_counts',
]  # fmt: skip
