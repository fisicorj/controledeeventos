"""Telas públicas: início, catálogo, página da atividade/evento e agenda (.ics)."""

from django.conf import settings
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from core.pagination import paginate
from core.permissions import can_manage
from registrations import services
from registrations.forms import RegistrationAnswersForm, RegistrationOptionsForm
from registrations.models import Registration

from .. import calendar
from ..models import Activity

CONFIRMED = Q(registrations__status=Registration.Status.CONFIRMED)


WAITLISTED = Q(registrations__status=Registration.Status.WAITLISTED)


def with_counts(queryset):
    return queryset.annotate(
        confirmed_count=Count('registrations', filter=CONFIRMED),
        waitlist_count=Count('registrations', filter=WAITLISTED),
    )


def activity_queryset():
    return with_counts(Activity.objects.filter(status=Activity.Status.PUBLISHED))


def upcoming_queryset():
    # Esconde atividades já encerradas; as em andamento continuam visíveis.
    return activity_queryset().filter(end_date__gte=timezone.now()).order_by('start_date')


def home(request):
    upcoming = list(upcoming_queryset()[:9])
    return render(
        request,
        'home.html',
        {'activities': upcoming, 'agenda': upcoming[:4], 'types': Activity.Type.choices},
    )


def activity_list(request):
    activities = upcoming_queryset()
    query = request.GET.get('q', '').strip()
    kind = request.GET.get('tipo', '')
    if query:
        activities = activities.filter(
            Q(title__icontains=query) | Q(description__icontains=query) | Q(location__icontains=query)
        )
    if kind in Activity.Type.values:
        activities = activities.filter(activity_type=kind)
    else:
        kind = ''
    page, querystring = paginate(request, activities, 12)
    context = {
        'activities': page.object_list,
        'page': page,
        'querystring': querystring,
        'query': query,
        'kind': kind,
        'types': Activity.Type.choices,
    }
    return render(request, 'activities/list.html', context)


def registration_state(activity, registration):
    """Diz se o botão de inscrição deve aparecer e, se não, por quê."""
    if registration and registration.offer_pending:
        return {'code': 'offer', 'deadline': registration.offer_expires_at}
    if registration and registration.status == Registration.Status.CONFIRMED:
        return {'code': 'registered'}
    if registration and registration.status == Registration.Status.WAITLISTED:
        return {'code': 'waitlisted', 'position': registration.waitlist_position()}
    if activity.status != Activity.Status.PUBLISHED:
        return {'code': 'blocked', 'reason': f'Atividade {activity.get_status_display().lower()}.'}
    now = timezone.now()
    if now < activity.registration_start:
        start = timezone.localtime(activity.registration_start).strftime('%d/%m/%Y às %H:%M')
        return {'code': 'blocked', 'reason': f'Inscrições abrem em {start}.'}
    if now > activity.registration_end:
        return {'code': 'blocked', 'reason': 'Inscrições encerradas.'}
    categories = list(activity.categories.all())
    if categories:
        counts = services.category_counts(activity)
        full = not any(services.has_room(activity, c, counts) for c in categories)
    else:
        full = activity.confirmed_count >= activity.capacity
    if full:
        if activity.waitlist_enabled:
            return {'code': 'waitlist'}
        return {'code': 'blocked', 'reason': 'Vagas esgotadas.'}
    return {'code': 'open'}


def activity_detail(request, pk, answers_form=None, options_form=None):
    """Endereço antigo (/activities/<id>/). Continua funcionando; a página aponta o endereço amigável como canônico."""
    return _detail(request, {'pk': pk}, answers_form, options_form)


def event_page(request, slug):
    return _detail(request, {'slug': slug})


def _registration_forms(request, activity, registration, answers_form, options_form):
    """Formulários de inscrição. Quem cancelou e vai se inscrever de novo encontra as respostas anteriores."""
    if answers_form is None:
        previous = {a.question_id: a.value for a in registration.answers.all()} if registration else {}
        answers_form = RegistrationAnswersForm(activity.questions.all(), initial_answers=previous)
    if options_form is None and request.user.is_authenticated:
        options_form = RegistrationOptionsForm(
            activity, request.user, initial={'category': registration.category_id if registration else None}
        )
    return answers_form, options_form


def _categories_with_room(activity):
    categories = list(activity.categories.all())
    if categories:
        counts = services.category_counts(activity)
        for category in categories:
            category.taken = counts.get(category.id, 0)
            category.remaining = max(category.capacity - category.taken, 0)
    return categories


def _visible_announcements(activity, registration):
    """Comunicados marcados para aparecer na página, conforme a situação da inscrição da pessoa."""
    if not registration or registration.status == Registration.Status.CANCELLED:
        return []
    return [a for a in activity.announcements.filter(show_on_page=True) if registration.status in a.statuses()]


def _detail(request, lookup, answers_form=None, options_form=None):
    activity = (
        with_counts(Activity.objects.prefetch_related('sessions__speakers', 'speakers'))
        .select_related('organizer')
        .filter(**lookup)
        .first()
    )
    if activity is None:
        raise Http404
    # Rascunhos, canceladas e finalizadas só aparecem para quem gerencia (pré-visualização).
    is_preview = activity.status != Activity.Status.PUBLISHED
    if is_preview and not can_manage(request.user, activity):
        raise Http404
    registration = None
    if request.user.is_authenticated:
        registration = activity.registrations.filter(user=request.user).first()
    answers_form, options_form = _registration_forms(request, activity, registration, answers_form, options_form)
    categories = _categories_with_room(activity)
    announcements = _visible_announcements(activity, registration)
    page_url = settings.SITE_URL + activity.get_absolute_url()
    return render(
        request,
        'activities/detail.html',
        {
            'activity': activity,
            'registration': registration,
            'state': registration_state(activity, registration),
            'is_preview': is_preview,
            'can_manage_activity': can_manage(request.user, activity),
            'answers_form': answers_form,
            'options_form': options_form,
            'categories': categories,
            'materials': _materials_for(request.user, activity) if request.user.is_authenticated else [],
            'announcements': announcements,
            'page_url': page_url,
            'calendar': calendar.links(activity, page_url),
            'map_url': calendar.map_url(activity.location),
            'og_image': settings.SITE_URL + activity.image.url if activity.image else '',
        },
    )


def calendar_ics(request, slug):
    activity = get_object_or_404(Activity.objects.prefetch_related('sessions'), slug=slug)
    if activity.status != Activity.Status.PUBLISHED and not can_manage(request.user, activity):
        raise Http404
    from core.models import SiteSettings

    content = calendar.ics(
        activity, settings.SITE_URL + activity.get_absolute_url(), SiteSettings.load().institution_name
    )
    response = HttpResponse(content, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{activity.slug}.ics"'
    return response


def with_delivery(announcements):
    """Andamento do envio de cada comunicado (enviados, na fila, com falha)."""
    from core.outbox import group_status

    announcements = list(announcements)
    status = group_status(*[f'announcement:{a.pk}' for a in announcements])
    for a in announcements:
        a.delivery = status[f'announcement:{a.pk}']
    return announcements


def _materials_for(user, activity):
    from ..operations import materials_for

    return materials_for(user, activity)
