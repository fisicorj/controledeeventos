"""Telas da chamada de trabalhos: autores, avaliadores e organização."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User
from activities.models import Activity
from core.csvsafe import excel_writer
from core.permissions import can_manage

from . import invites, services
from .forms import CallForm, DecisionForm, DeclineForm, ReopenForm, ReviewForm, SubmissionForm
from .models import CallForPapers, Review, ReviewerInvite, Submission

Status = Submission.Status


# ---------- Autores


def call_page(request, slug):
    """Página pública da chamada (o evento precisa estar publicado; a organização vê a prévia)."""
    activity = get_object_or_404(Activity.objects.select_related('call_for_papers'), slug=slug)
    call = getattr(activity, 'call_for_papers', None)
    managing = call is not None and services.can_manage_call(request.user, call)
    if call is None or not (call.is_visible or managing):
        raise Http404
    mine = []
    if request.user.is_authenticated:
        mine = list(call.submissions.filter(author=request.user))
    accepted = call.submissions.filter(status=Status.ACCEPTED).select_related('author', 'session').order_by('title')
    return render(
        request,
        'submissions/call.html',
        {
            'activity': activity,
            'call': call,
            'mine': mine,
            'accepted': accepted,
            'is_preview': not call.is_visible,
            'now': timezone.now(),
        },
    )


@login_required
def submission_create(request, slug):
    activity = get_object_or_404(Activity.objects.select_related('call_for_papers'), slug=slug)
    call = getattr(activity, 'call_for_papers', None)
    if call is None or not call.is_visible:
        raise Http404
    if not call.is_open:
        messages.error(request, 'As submissões desta chamada não estão abertas.')
        return redirect('call_page', slug=slug)
    if not request.user.email_verified:
        messages.warning(request, 'Confirme o seu e-mail antes de enviar um trabalho.')
        return redirect('verify_email')
    sent = call.submissions.filter(author=request.user).exclude(status=Status.WITHDRAWN).count()
    if sent >= call.max_submissions:
        messages.error(
            request,
            f'Você já enviou {sent} trabalho(s), o máximo desta chamada. Para enviar outro, retire um dos atuais.',
        )
        return redirect('call_page', slug=slug)
    form = SubmissionForm(request.POST or None, request.FILES or None, call=call, author_email=request.user.email)
    if request.method == 'POST' and _upload_blocked(request):
        form.is_valid()
        form.add_error(None, UPLOAD_LIMIT_TEXT)
    elif form.is_valid():
        with transaction.atomic():
            submission = form.save(commit=False)
            submission.call, submission.author = call, request.user
            submission.file_name = request.FILES['file'].name[:200]
            submission.save()
            services.received(submission)
        messages.success(request, 'Trabalho enviado. Você recebe por e-mail cada etapa da avaliação.')
        return redirect('submission_detail', pk=submission.pk)
    return render(request, 'submissions/form.html', {'form': form, 'call': call, 'activity': activity})


UPLOADS_PER_HOUR = 10
UPLOAD_LIMIT_TEXT = 'Muitos envios de arquivo em pouco tempo. Espere uma hora e tente de novo.'


def _upload_blocked(request):
    """Conta um envio de arquivo da pessoa; True quando passou de UPLOADS_PER_HOUR na última hora."""
    from core import throttle

    return not throttle.hit(UPLOADS_PER_HOUR, 3600, 'submission-upload', request.user.pk)


def _own(request, pk):
    return get_object_or_404(
        Submission.objects.select_related('call__activity', 'author', 'session'), pk=pk, author=request.user
    )


@login_required
def submission_detail(request, pk):
    submission = _own(request, pk)
    final = submission.status in (Status.ACCEPTED, Status.REJECTED, Status.REVISION)
    comments = [r for r in submission.reviews.all() if r.is_done and r.comments_to_author.strip()] if final else []
    return render(
        request,
        'submissions/detail.html',
        {'submission': submission, 'call': submission.call, 'activity': submission.call.activity, 'comments': comments},
    )


@login_required
def submission_edit(request, pk):
    submission = _own(request, pk)
    if not submission.author_can_edit:
        messages.error(request, 'Este trabalho não pode mais ser alterado.')
        return redirect('submission_detail', pk=submission.pk)
    revision = submission.status == Status.REVISION
    form = SubmissionForm(request.POST or None, request.FILES or None, instance=submission)
    if revision:
        form.fields['file'].required = True
        form.fields['file'].help_text = 'Envie a versão corrigida. ' + form.fields['file'].help_text
    previous_file = submission.file.name
    if request.method == 'POST' and revision and 'file' not in request.FILES:
        form.is_valid()
        form.add_error('file', 'Envie o arquivo da versão corrigida.')
    elif request.method == 'POST' and 'file' in request.FILES and _upload_blocked(request):
        form.is_valid()
        form.add_error(None, UPLOAD_LIMIT_TEXT)
    elif form.is_valid():
        with transaction.atomic():
            submission = form.save(commit=False)
            if 'file' in request.FILES:
                submission.file_name = request.FILES['file'].name[:200]
            submission.save()
            if 'file' in request.FILES and previous_file and previous_file != submission.file.name:
                # O arquivo trocado não tem mais uso: sai do disco quando a gravação se confirmar.
                storage = submission.file.storage
                transaction.on_commit(lambda: storage.delete(previous_file))
            if revision:
                services.resubmitted(submission)
        messages.success(request, 'Versão corrigida enviada para avaliação.' if revision else 'Trabalho atualizado.')
        return redirect('submission_detail', pk=submission.pk)
    return render(
        request,
        'submissions/form.html',
        {
            'form': form,
            'call': submission.call,
            'activity': submission.call.activity,
            'submission': submission,
            'revision': revision,
        },
    )


@login_required
def submission_withdraw(request, pk):
    submission = _own(request, pk)
    if request.method == 'POST':
        if services.withdraw(submission):
            messages.success(request, 'Trabalho retirado da avaliação.')
        else:
            messages.error(request, 'Este trabalho já tem decisão e não pode ser retirado. Fale com a organização.')
    return redirect('submission_detail', pk=submission.pk)


@login_required
def my_submissions(request):
    submissions = Submission.objects.filter(author=request.user).select_related('call__activity')
    return render(request, 'submissions/mine.html', {'submissions': submissions})


@login_required
def submission_file(request, pk):
    """O arquivo só sai para o autor, a organização e os avaliadores designados (sem o nome original, às cegas)."""
    submission = get_object_or_404(Submission.objects.select_related('call__activity'), pk=pk)
    user = request.user
    is_author = submission.author_id == user.id
    managing = services.can_manage_call(user, submission.call)
    if not (is_author or managing or services.can_review(user, submission)):
        raise Http404
    if not submission.file:  # apagado na exclusão da conta do autor
        raise Http404
    try:
        handle = submission.file.open('rb')
    except (FileNotFoundError, ValueError) as exc:
        raise Http404 from exc
    extension = submission.file.name.rsplit('.', 1)[-1].lower()
    name = (
        submission.file_name
        if is_author or managing or not submission.call.blind_review
        else f'trabalho-{submission.pk}-v{submission.version}.{extension}'
    )
    content_type = mimetypes.guess_type(f'x.{extension}')[0] or 'application/octet-stream'
    inline = content_type == 'application/pdf'
    response = FileResponse(
        handle,
        as_attachment=not inline,
        filename=name,
        content_type=content_type if inline else 'application/octet-stream',
    )
    response['X-Content-Type-Options'] = 'nosniff'
    return response


# ---------- Avaliadores


@login_required
def my_reviews(request):
    from core.context_processors import forget_menu

    forget_menu(request)  # esta tela é o destino depois de entregar, recusar ou aceitar: o menu se atualiza aqui
    reviews = (
        Review.objects.filter(reviewer=request.user)
        .exclude(submission__status=Status.WITHDRAWN)
        .select_related('submission__call__activity')
        .order_by('submitted_at', '-assigned_at')
    )
    coordinated = CallForPapers.objects.filter(coordinators=request.user).select_related('activity')
    return render(
        request,
        'submissions/reviews.html',
        {'reviews': reviews, 'coordinated': coordinated.order_by('-activity__start_date')},
    )


@login_required
def review_form(request, pk):
    review = get_object_or_404(
        Review.objects.select_related('submission__call__activity', 'submission__author'), pk=pk, reviewer=request.user
    )
    submission = review.submission
    if submission.author_id == request.user.id:
        raise Http404
    editable = not submission.is_final and submission.status != Status.REVISION
    data = request.POST if request.method == 'POST' and 'decline' not in request.POST else None
    form = ReviewForm(data, instance=review, criteria=submission.call.criteria_list())
    decline_form = DeclineForm(request.POST if 'decline' in request.POST else None)
    if request.method == 'POST' and 'decline' in request.POST:
        if review.is_done or not editable:
            messages.error(request, 'Esta avaliação não pode mais ser recusada.')
            return redirect('review_form', pk=review.pk)
        if decline_form.is_valid():
            services.decline_review(review, decline_form.cleaned_data['reason'], decline_form.cleaned_data['note'])
            messages.success(request, 'Recusa registrada. A organização foi avisada e vai designar outra pessoa.')
            return redirect('my_reviews')
    elif request.method == 'POST':
        if not editable:
            messages.error(request, 'Este trabalho já tem decisão: a avaliação não pode mais mudar.')
            return redirect('review_form', pk=review.pk)
        if form.is_valid():
            with transaction.atomic():
                services.submit_review(form.save(commit=False))
            messages.success(request, 'Avaliação registrada. Obrigado!')
            return redirect('my_reviews')
    return render(
        request,
        'submissions/review_form.html',
        {
            'review': review,
            'submission': submission,
            'call': submission.call,
            'form': form,
            'editable': editable,
            'decline_form': decline_form,
            'show_authors': not submission.call.blind_review,
        },
    )


# ---------- Organização


def _managed_activity(request, activity_id):
    """Só a organização do evento (criar a chamada, mudar a coordenação)."""
    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    return activity


def _managed_call(request, activity_id):
    """Chamada que a pessoa cuida: organização do evento ou coordenação da chamada (R2)."""
    call = get_object_or_404(CallForPapers.objects.select_related('activity'), activity_id=activity_id)
    if not services.can_manage_call(request.user, call):
        raise PermissionDenied('Acesso restrito.')
    return call.activity, call


@login_required
def call_edit(request, activity_id):
    call = CallForPapers.objects.filter(activity_id=activity_id).select_related('activity').first()
    if call is None:
        activity = _managed_activity(request, activity_id)  # abrir a chamada é da organização do evento
    else:
        activity, call = _managed_call(request, activity_id)
    if call is None and activity.status in (Activity.Status.CANCELLED, Activity.Status.FINISHED):
        messages.error(request, 'Atividade encerrada ou cancelada não abre chamada de trabalhos.')
        return redirect('activity_manage', pk=activity.pk)
    initial = {}
    if call is None:
        initial = {'opens_at': None, 'closes_at': activity.start_date}
    form = CallForm(request.POST or None, instance=call, activity=activity, initial=initial)
    if form.is_valid():
        form.save()
        messages.success(request, 'Chamada de trabalhos salva.')
        if activity.status != Activity.Status.PUBLISHED:
            messages.info(request, 'A chamada aparece para os autores quando a atividade for publicada.')
        return redirect('call_manage', activity_id=activity.pk)
    return render(
        request,
        'submissions/call_form.html',
        {
            'form': form,
            'activity': activity,
            'heading': 'Chamada de trabalhos' if call else 'Abrir chamada de trabalhos',
        },
    )


@login_required
def call_manage(request, activity_id):
    if not CallForPapers.objects.filter(activity_id=activity_id).exists():
        activity = _managed_activity(request, activity_id)
        return redirect('call_edit', activity_id=activity.pk)
    activity, call = _managed_call(request, activity_id)
    status = request.GET.get('situacao', '')
    submissions = call.submissions.select_related('author', 'session').annotate(
        assigned=Count('reviews', distinct=True),
        done=Count('reviews', filter=Q(reviews__submitted_at__isnull=False), distinct=True),
        average=Avg('reviews__score', filter=Q(reviews__submitted_at__isnull=False)),
    )
    if status in Status.values:
        submissions = submissions.filter(status=status)
    else:
        status = ''
    counts = dict(call.submissions.values_list('status').annotate(n=Count('id')))
    now = timezone.now()
    open_review = Q(
        reviews__submission__call=call,
        reviews__submitted_at__isnull=True,
        reviews__submission__status__in=services.OPEN_FOR_REVIEW,
    )
    reviewers = list(
        call.reviewers.annotate(
            load=Count('reviews', filter=Q(reviews__submission__call=call), distinct=True),
            pending=Count('reviews', filter=open_review, distinct=True),
            overdue=Count('reviews', filter=open_review & Q(reviews__due_at__lt=now), distinct=True),
        ).order_by('first_name', 'username')
    )
    areas = services.reviewer_areas(call)
    for person in reviewers:
        person.areas = areas.get(person.pk, [])
    waiting = call.submissions.filter(status__in=services.OPEN_FOR_REVIEW).count()
    return render(
        request,
        'submissions/manage.html',
        {
            'activity': activity,
            'call': call,
            'submissions': submissions.order_by('status', '-created_at'),
            'status': status,
            'statuses': [(value, label, counts.get(value, 0)) for value, label in Status.choices],
            'reviewers': reviewers,
            'call_areas': call.area_list(),
            'waiting': waiting,
            'overdue_total': sum(p.overdue for p in reviewers),
            'is_organizer': can_manage(request.user, activity),
            'coordinators': call.coordinators.order_by('first_name', 'username'),
            'invites': [i for i in call.reviewer_invites.filter(accepted_at__isnull=True) if i.is_open],
            'other_calls': [c for c in services.calls_for(request.user) if c.pk != call.pk and c.reviewers.exists()],
        },
    )


@login_required
@require_POST
def reviewer_remind(request, activity_id, user_id):
    """Lembrete na hora para um avaliador, com todas as avaliações pendentes dele nesta chamada."""
    activity, call = _managed_call(request, activity_id)
    person = get_object_or_404(call.reviewers, pk=user_id)
    with transaction.atomic():
        count = services.remind_reviewer_now(call, person)
    name = person.get_full_name() or person.username
    if count:
        messages.success(request, f'Lembrete enviado a {name} ({count} avaliação(ões) pendente(s)).')
    else:
        messages.info(request, f'{name} não tem avaliação pendente.')
    return redirect('call_manage', activity_id=activity.pk)


@login_required
@require_POST
def reviewer_set_areas(request, activity_id, user_id):
    """Áreas que o avaliador aceita (nenhuma marcada: todas), usadas na distribuição automática."""
    activity, call = _managed_call(request, activity_id)
    person = get_object_or_404(call.reviewers, pk=user_id)
    chosen = services.set_reviewer_areas(call, person, request.POST.getlist('areas'))
    name = person.get_full_name() or person.username
    messages.success(request, f'{name}: ' + (', '.join(chosen) if chosen else 'todas as áreas') + '.')
    return redirect('call_manage', activity_id=activity.pk)


@login_required
@require_POST
def call_auto_assign(request, activity_id):
    """Distribui os trabalhos que esperam avaliação entre os avaliadores (área, conflito e carga)."""
    activity, call = _managed_call(request, activity_id)
    try:
        per_submission = max(1, min(5, int(request.POST.get('per_submission', '2'))))
    except ValueError:
        per_submission = 2
    created, short = services.auto_assign(call, per_submission)
    if created:
        messages.success(
            request,
            f'{created} avaliação(ões) designada(s), {per_submission} avaliador(es) por trabalho. Os avaliadores '
            'foram avisados por e-mail. Ajuste qualquer trabalho na tela dele.',
        )
    else:
        messages.info(request, 'Nada a distribuir: os trabalhos já têm avaliadores suficientes.')
    if short:
        titles = ', '.join(f'"{w.title}"' for w in short[:5]) + (' e outros' if len(short) > 5 else '')
        messages.warning(
            request,
            f'{len(short)} trabalho(s) ficaram com menos avaliadores que o pedido (faltam avaliadores da área ou '
            f'sem conflito): {titles}. Adicione avaliadores ou designe na tela do trabalho.',
        )
    return redirect('call_manage', activity_id=activity.pk)


@login_required
def reviewer_add(request, activity_id):
    """Inclui um avaliador pela conta (usuário ou e-mail confirmado) ou, sem conta, convida pelo e-mail (R4)."""
    activity, call = _managed_call(request, activity_id)
    if request.method == 'POST':
        typed = request.POST.get('person', '').strip()
        person = _find_person(typed)
        if person is not None:
            if call.reviewers.filter(pk=person.pk).exists():
                messages.info(request, f'{person.get_full_name() or person.username} já é avaliador desta chamada.')
            else:
                call.reviewers.add(person)
                services.notifications.notify_reviewer_added(call, person, request.user)
                messages.success(request, f'{person.get_full_name() or person.username} entrou como avaliador.')
        elif _looks_like_email(typed) and not invites.within_limit(request.user):
            messages.error(
                request,
                f'Limite de {invites.INVITES_PER_HOUR} convites por hora atingido. Tente de novo mais tarde.',
            )
        elif _looks_like_email(typed):
            invites.create(call, typed, request.user)
            messages.success(
                request,
                f'Não há conta confirmada com {typed}: enviamos um convite. Quando a pessoa aceitar, ela entra '
                'para os avaliadores.',
            )
        else:
            messages.error(
                request,
                f'Não encontrei uma conta com o usuário "{typed}". Para quem não tem conta, informe o e-mail: '
                'a pessoa recebe um convite.',
            )
    return redirect('call_manage', activity_id=activity.pk)


def _find_person(typed):
    if not typed:
        return None
    return (
        User.objects.filter(is_active=True)
        .filter(Q(username__iexact=typed) | Q(email__iexact=typed, email_verified=True))
        .first()
    )


def _looks_like_email(typed):
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    try:
        validate_email(typed)
    except ValidationError:
        return False
    return True


@login_required
@require_POST
def reviewer_invite_cancel(request, activity_id, invite_id):
    activity, call = _managed_call(request, activity_id)
    invite = get_object_or_404(ReviewerInvite, pk=invite_id, call=call)
    if invites.cancel(invite):
        messages.success(request, f'Convite para {invite.email} cancelado.')
    return redirect('call_manage', activity_id=activity.pk)


@login_required
@require_POST
def reviewer_invite_resend(request, activity_id, invite_id):
    activity, call = _managed_call(request, activity_id)
    invite = get_object_or_404(ReviewerInvite, pk=invite_id, call=call, accepted_at__isnull=True)
    if not invites.within_limit(request.user):
        messages.error(request, f'Limite de {invites.INVITES_PER_HOUR} convites por hora atingido. Tente mais tarde.')
        return redirect('call_manage', activity_id=activity.pk)
    invites.create(call, invite.email, request.user)  # link novo; o anterior deixa de valer
    messages.success(request, f'Convite reenviado para {invite.email}.')
    return redirect('call_manage', activity_id=activity.pk)


def reviewer_invite_accept(request, token):
    """Página pública do link do convite de avaliador: entra com a conta atual ou cria uma."""
    from django.contrib.auth import login

    from accounts.forms import InviteAcceptForm
    from core import throttle
    from core.http import client_ip

    ip = client_ip(request) or '-'
    if throttle.blocked(INVITE_MISSES, INVITE_PERIOD, 'reviewer-invite-miss', ip):
        return render(request, 'submissions/reviewer_invite.html', {'invalid': True}, status=429)
    invite = invites.find(token)
    if invite is None:
        throttle.hit(INVITE_MISSES, INVITE_PERIOD, 'reviewer-invite-miss', ip)
        return render(request, 'submissions/reviewer_invite.html', {'invalid': True}, status=404)
    if request.user.is_authenticated:
        # Link repassado: a conta logada tem outro e-mail. Mostra o e-mail do convite e pede confirmação (S-06).
        other_email = (request.user.email or '').lower() != invite.email.lower()
        context = {'invite': invite, 'other_email': other_email}
        if request.method == 'POST' and other_email and not request.POST.get('confirm_other'):
            context['confirm_error'] = True
            return render(request, 'submissions/reviewer_invite.html', context)
        if request.method == 'POST':
            try:
                call = invites.accept(invite, request.user)
            except invites.Refused as exc:
                messages.error(request, str(exc))
                return redirect('my_reviews')
            messages.success(request, f'Pronto: você é avaliador da chamada de {call.activity.title}.')
            return redirect('my_reviews')
        return render(request, 'submissions/reviewer_invite.html', context)
    form = InviteAcceptForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            user = invites.create_account(invite, form)
        except invites.Refused as exc:
            messages.error(request, str(exc))
            return redirect(f'{reverse("login")}?next={request.path}')
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, f'Conta criada. Você já é avaliador da chamada de {invite.call.activity.title}.')
        return redirect('my_reviews')
    return render(request, 'submissions/reviewer_invite.html', {'invite': invite, 'form': form})


INVITE_MISSES, INVITE_PERIOD = 20, 3600


@login_required
@require_POST
def reviewers_copy(request, activity_id):
    """Traz os avaliadores (e as áreas em comum) de outra chamada que a pessoa cuida (R3)."""
    activity, call = _managed_call(request, activity_id)
    source = get_object_or_404(CallForPapers, pk=request.POST.get('source') or 0)
    if source.pk == call.pk or not services.can_manage_call(request.user, source):
        raise PermissionDenied('Acesso restrito.')
    added, unmatched = services.copy_reviewers(source, call)
    if added:
        messages.success(request, f'{len(added)} avaliador(es) trazido(s) de {source.activity.title}.')
    else:
        messages.info(request, 'Todos os avaliadores daquela chamada já estão nesta.')
    if unmatched:
        names = ', '.join(p.get_full_name() or p.username for p in unmatched)
        messages.warning(
            request, f'As áreas destes avaliadores não existem nesta chamada, então ficaram com todas: {names}.'
        )
    return redirect('call_manage', activity_id=activity.pk)


@login_required
@require_POST
def coordinator_add(request, activity_id):
    """Coordenação da chamada (R2): só a organização do evento inclui e tira."""
    activity = _managed_activity(request, activity_id)
    call = get_object_or_404(CallForPapers, activity=activity)
    typed = request.POST.get('person', '').strip()
    person = _find_person(typed)
    if person is None:
        messages.error(
            request,
            f'Não encontrei uma conta com o usuário ou e-mail "{typed}". A pessoa precisa ter conta no portal '
            '(convide-a como avaliadora, se preciso, e depois inclua na coordenação).',
        )
    elif person.pk == activity.organizer_id:
        messages.info(request, 'A organização do evento já cuida da chamada.')
    else:
        call.coordinators.add(person)
        services.notifications.notify_coordinator_added(call, person, request.user)
        messages.success(request, f'{person.get_full_name() or person.username} entrou na coordenação da chamada.')
    return redirect('call_manage', activity_id=activity.pk)


@login_required
@require_POST
def coordinator_remove(request, activity_id, user_id):
    activity = _managed_activity(request, activity_id)
    call = get_object_or_404(CallForPapers, activity=activity)
    person = call.coordinators.filter(pk=user_id).first()
    if person:
        call.coordinators.remove(person)
        messages.success(request, f'{person.get_full_name() or person.username} saiu da coordenação.')
    return redirect('call_manage', activity_id=activity.pk)


@login_required
def reviewer_remove(request, activity_id, user_id):
    activity, call = _managed_call(request, activity_id)
    if request.method == 'POST':
        person = call.reviewers.filter(pk=user_id).first()
        if person:
            pending = Review.objects.filter(submission__call=call, reviewer=person, submitted_at__isnull=True)
            removed = pending.count()
            pending.delete()
            call.reviewers.remove(person)
            text = f'{person.get_full_name() or person.username} saiu da lista de avaliadores.'
            if removed:
                text += f' {removed} avaliação(ões) pendente(s) foram retiradas; designe outra pessoa.'
            messages.success(request, text)
    return redirect('call_manage', activity_id=activity.pk)


@login_required
def submission_manage(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related('call__activity', 'author', 'session', 'decided_by'), pk=pk
    )
    activity = submission.call.activity
    if not services.can_manage_call(request.user, submission.call):
        raise PermissionDenied('Acesso restrito.')
    call = submission.call
    decision_form = DecisionForm(request.POST if request.POST.get('action') == 'decide' else None)
    reopen_form = ReopenForm(request.POST if request.POST.get('action') == 'reopen' else None)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'assign':
            return _assign(request, submission, call)
        if action == 'session':
            return _set_session(request, submission, activity)
        if action == 'decide' and decision_form.is_valid():
            data = decision_form.cleaned_data
            try:
                submission, registration = services.decide(submission, data['decision'], data['note'], request.user)
            except services.Refused as exc:
                messages.error(request, str(exc))
                return redirect('submission_manage', pk=submission.pk)
            text = f'Decisão registrada: {submission.get_status_display().lower()}. O autor foi avisado por e-mail.'
            if registration:
                text += ' Ele já está inscrito no evento.'
            elif submission.status == Status.ACCEPTED:
                text += ' O evento não está mais aberto, então não houve inscrição a fazer.'
            messages.success(request, text)
            return redirect('submission_manage', pk=submission.pk)
        if action == 'reopen' and reopen_form.is_valid():
            try:
                services.reopen(submission, reopen_form.cleaned_data['note'], request.user)
            except services.Refused as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    'Avaliação reaberta e autor avisado. Se ele tinha sido inscrito pela aprovação, a inscrição '
                    'continua: cancele-a pela gestão da atividade, se for o caso.',
                )
            return redirect('submission_manage', pk=submission.pk)
    reviews = list(submission.reviews.select_related('reviewer'))
    assigned = {r.reviewer_id for r in reviews}
    return render(
        request,
        'submissions/manage_detail.html',
        {
            'submission': submission,
            'activity': activity,
            'call': call,
            'reviews': reviews,
            'reviewer_choices': [
                (person, person.pk in assigned)
                for person in call.reviewers.exclude(pk=submission.author_id).order_by('first_name', 'username')
            ],
            'decision_form': decision_form,
            'reopen_form': reopen_form,
            'can_reopen': submission.status in services.REOPENABLE,
            'events': submission.events.select_related('actor'),
            'conflicts': services.possible_conflicts(submission, [r.reviewer for r in reviews]),
            'declines': submission.declines.select_related('reviewer'),
            'is_organizer': can_manage(request.user, activity),
            'criteria': call.criteria_list(),
            'sessions': activity.sessions.all(),
        },
    )


def _assign(request, submission, call):
    if submission.is_final:
        messages.error(request, 'Este trabalho já tem decisão.')
        return redirect('submission_manage', pk=submission.pk)
    ids = [int(v) for v in request.POST.getlist('reviewers') if v.isdigit()]
    people = list(call.reviewers.filter(pk__in=ids))
    added, removed = services.assign(submission, people)
    declined = set(submission.declines.values_list('reviewer_id', flat=True))
    for person in (r.reviewer for r in added if r.reviewer_id in declined):
        name = person.get_full_name() or person.username
        messages.warning(request, f'{name} tinha recusado este trabalho e foi designado de novo.')
    for person in services.possible_conflicts(submission, people):
        messages.warning(
            request,
            f'{person.get_full_name()} aparece entre os coautores do trabalho. '
            'Confira se não há conflito de interesse.',
        )
    messages.success(
        request,
        f'Avaliadores atualizados: {len(added)} incluído(s), {len(removed)} retirado(s). Os novos foram avisados.',
    )
    return redirect('submission_manage', pk=submission.pk)


def _set_session(request, submission, activity):
    value = request.POST.get('session', '')
    session = activity.sessions.filter(pk=value).first() if value.isdigit() else None
    services.set_session(submission, session)
    messages.success(
        request, f'Trabalho colocado em "{session.title}".' if session else 'Trabalho sem sessão definida.'
    )
    return redirect('submission_manage', pk=submission.pk)


@login_required
def submissions_csv(request, activity_id):
    activity, call = _managed_call(request, activity_id)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="trabalhos-{activity.slug}.csv"'
    response.write('﻿')
    writer = excel_writer(response)
    criteria = call.criteria_list()
    writer.writerow(
        ['Título', 'Área', 'Autor', 'E-mail', 'Coautores', 'Situação', 'Versão', 'Avaliações', 'Nota média', 'Sessão']
        + [f'Média: {name}' for name in criteria]
    )
    rows = (
        call.submissions.select_related('author', 'session')
        .prefetch_related('reviews')
        .annotate(
            done=Count('reviews', filter=Q(reviews__submitted_at__isnull=False)),
            average=Avg('reviews__score', filter=Q(reviews__submitted_at__isnull=False)),
        )
    )
    for s in rows.order_by('title'):
        writer.writerow(
            [
                s.title,
                s.area,
                s.author.get_full_name() or s.author.username,
                s.author.email,
                '; '.join(s.coauthor_list()),
                s.get_status_display(),
                s.version,
                s.done,
                f'{s.average:.1f}'.replace('.', ',') if s.average is not None else '',
                s.session.title if s.session else '',
            ]
            + [_criterion_average(s, name) for name in criteria]
        )
    return response


def _criterion_average(submission, name):
    scores = [r.criteria_scores.get(name) for r in submission.reviews.all() if r.is_done]
    scores = [v for v in scores if isinstance(v, int)]
    return f'{sum(scores) / len(scores):.1f}'.replace('.', ',') if scores else ''
