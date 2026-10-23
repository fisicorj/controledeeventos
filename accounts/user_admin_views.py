"""Telas de Configurações > Usuários (regras em accounts/user_admin.py)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.pagination import paginate
from core.permissions import is_platform_admin

from . import user_admin
from .models import MfaDevice, User, UserChange
from .reauth import require_recent_auth

FILTERS = {
    'situacao': {'ativos': Q(is_active=True), 'inativos': Q(is_active=False)},
    'email': {'confirmado': Q(email_verified=True), 'pendente': Q(email_verified=False)},
    'duas_etapas': {'sim': Q(has_mfa=True), 'nao': Q(has_mfa=False)},
}


def _admin_only(request):
    if not is_platform_admin(request.user):
        raise PermissionDenied('Acesso restrito a administradores.')


def _users():
    confirmed = MfaDevice.objects.filter(user=OuterRef('pk'), confirmed_at__isnull=False)
    return User.objects.annotate(has_mfa=Exists(confirmed))


def _filtered(request):
    users = _users()
    query = request.GET.get('q', '').strip()
    if query:
        users = users.filter(
            Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
            | Q(email__icontains=query) | Q(institutional_id__icontains=query)
        )  # fmt: skip
    role = request.GET.get('papel', '')
    if role in User.Role.values:
        users = users.filter(role=role)
    for param, options in FILTERS.items():
        condition = options.get(request.GET.get(param, ''))
        if condition is not None:
            users = users.filter(condition)
    return users.order_by('first_name', 'last_name', 'username'), query, role


@login_required
def user_list(request):
    _admin_only(request)
    users, query, role = _filtered(request)
    page, querystring = paginate(request, users, 50)
    counts = dict(User.objects.filter(is_active=True).values_list('role').annotate(n=Count('pk')))
    return render(
        request,
        'accounts/user_admin/list.html',
        {
            'page': page,
            'querystring': querystring,
            'query': query,
            'roles': User.Role.choices,
            'selected': {'papel': role, **{p: request.GET.get(p, '') for p in FILTERS}},
            'counts': [(label, counts.get(value, 0)) for value, label in User.Role.choices],
            'total': page.paginator.count,
        },
    )


@login_required
def user_detail(request, pk):
    from activities.models import Activity
    from certificates.history import certificates_of
    from certificates.models import CertificateBatch
    from registrations.models import Registration

    from .verification import pending

    _admin_only(request)
    person = get_object_or_404(_users(), pk=pk)
    registrations = Registration.objects.filter(user=person)
    receivers = (
        User.objects.filter(is_active=True, deleted_at__isnull=True)
        .filter(Q(is_superuser=True) | ~Q(role=User.Role.PARTICIPANT))
        .exclude(pk=person.pk)
        .order_by('first_name', 'last_name', 'username')
    )
    return render(
        request,
        'accounts/user_admin/detail.html',
        {
            'person': person,
            'roles': User.Role.choices,
            'registrations': registrations.count(),
            'confirmed_registrations': registrations.filter(status=Registration.Status.CONFIRMED).count(),
            'certificates': certificates_of(person).count(),
            'activities': Activity.objects.filter(organizer=person).count(),
            'batches': CertificateBatch.objects.filter(created_by=person).count(),
            'pending_code': pending(person),
            'history': UserChange.objects.filter(target=person).select_related('actor')[:30],
            'is_self': person.pk == request.user.pk,
            'locked': person.is_superuser and not request.user.is_superuser,
            'deleted': person.is_deleted,
            'receivers': receivers,
        },
    )


def _action(request, pk, run, success):
    """Executa uma ação do painel: confere o acesso, aplica a regra e mostra o resultado."""
    _admin_only(request)
    person = get_object_or_404(User, pk=pk)
    try:
        result = run(person)
    except user_admin.Refused as exc:
        messages.error(request, str(exc))
    else:
        if result is not False:
            messages.success(request, success(person) if callable(success) else success)
    return redirect('user_admin_detail', pk=person.pk)


@login_required
@require_POST
@require_recent_auth
def user_role(request, pk):
    role = request.POST.get('role', '')
    label = dict(User.Role.choices).get(role, role)
    return _action(
        request, pk, lambda p: user_admin.change_role(request.user, p, role, request), f'Papel alterado para {label}.'
    )


@login_required
@require_POST
@require_recent_auth
def user_active(request, pk):
    active = request.POST.get('active') == '1'
    text = 'Conta reativada.' if active else 'Conta desativada; as sessões abertas foram encerradas.'
    return _action(request, pk, lambda p: user_admin.set_active(request.user, p, active, request), text)


@login_required
@require_POST
@require_recent_auth
def user_mfa_reset(request, pk):
    return _action(
        request,
        pk,
        lambda p: user_admin.reset_mfa(request.user, p, request),
        'Verificação em duas etapas apagada e sessões encerradas. No próximo acesso a pessoa cadastra de novo.',
    )


@login_required
@require_POST
@require_recent_auth
def user_password_link(request, pk):
    return _action(
        request,
        pk,
        lambda p: user_admin.send_password_link(request.user, p, request),
        lambda p: f'Link de nova senha enviado para {p.email}.',
    )


@login_required
@require_POST
@require_recent_auth
def user_resend_code(request, pk):
    return _action(
        request,
        pk,
        lambda p: user_admin.resend_code(request.user, p, request),
        lambda p: f'Código de confirmação reenviado para {p.email}.',
    )


@login_required
@require_POST
@require_recent_auth
def user_delete(request, pk):
    _admin_only(request)
    if request.POST.get('confirm', '').strip().upper() != 'EXCLUIR':
        messages.error(request, 'Digite EXCLUIR para confirmar a exclusão.')
        return redirect('user_admin_detail', pk=pk)
    return _action(
        request,
        pk,
        lambda p: user_admin.delete_account(request.user, p, request),
        'Conta excluída: os dados pessoais foram apagados; inscrições, presenças e certificados continuam no '
        'histórico.',
    )


CSV_HEADER = ['Usuário', 'Nome', 'E-mail', 'E-mail confirmado', 'Matrícula ou CPF', 'Papel', 'Superusuário',
              'Situação', 'Duas etapas', 'Cadastro', 'Último acesso']  # fmt: skip


@login_required
@require_recent_auth
def user_list_csv(request):
    """A lista filtrada em planilha, para a conferência periódica de quem tem papel de equipe. Tem dados pessoais
    de todo mundo: pede a senha de novo, como as outras ações do painel."""
    from django.http import HttpResponse
    from django.utils import timezone

    from core.csvsafe import excel_writer

    _admin_only(request)
    users, _, _ = _filtered(request)

    def when(value):
        return timezone.localtime(value).strftime('%d/%m/%Y %H:%M') if value else ''

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="usuarios-{timezone.localdate():%Y%m%d}.csv"'
    response.write('\ufeff')  # o Excel reconhece acentos
    writer = excel_writer(response)
    writer.writerow(CSV_HEADER)
    for u in users.iterator():
        situation = 'Excluída' if u.is_deleted else ('Ativa' if u.is_active else 'Desativada')
        writer.writerow([
            u.username, u.get_full_name(), u.email, 'sim' if u.email_verified else 'não', u.institutional_id,
            u.get_role_display(), 'sim' if u.is_superuser else 'não', situation, 'sim' if u.has_mfa else 'não',
            when(u.date_joined), when(u.last_login),
        ])  # fmt: skip
    return response


@login_required
@require_POST
@require_recent_auth
def user_transfer(request, pk):
    _admin_only(request)
    receiver = get_object_or_404(User, pk=request.POST.get('to') or 0)

    def run(person):
        signs_open = request.POST.get('assinatura') == 'nova'
        activities, batches = user_admin.transfer_ownership(
            request.user, person, receiver, request, receiver_signs_open=signs_open
        )
        return activities, batches

    name = receiver.get_full_name() or receiver.username
    return _action(request, pk, run, lambda p: f'Atividades e emissões avulsas transferidas para {name}.')


# ---------- Convites para a equipe (accounts/invites.py)


@login_required
@require_recent_auth
def invite_list(request):
    from django import forms

    from . import invites
    from .models import StaffInvite

    _admin_only(request)

    class InviteForm(forms.Form):
        email = forms.EmailField(label='E-mail', widget=forms.EmailInput(attrs={'class': 'form-control'}))
        role = forms.ChoiceField(
            label='Papel',
            choices=[(r.value, r.label) for r in invites.STAFF_ROLES],
            widget=forms.Select(attrs={'class': 'form-select'}),
        )

    form = InviteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            invites.create(request.user, form.cleaned_data['email'], form.cleaned_data['role'], request)
        except user_admin.Refused as exc:
            form.add_error('email', str(exc))
        else:
            messages.success(request, f'Convite enviado para {form.cleaned_data["email"]}.')
            return redirect('user_admin_invites')
    return render(
        request,
        'accounts/user_admin/invites.html',
        {'form': form, 'invites': StaffInvite.objects.select_related('created_by', 'accepted_user')[:50]},
    )


@login_required
@require_POST
@require_recent_auth
def invite_cancel(request, pk):
    from . import invites
    from .models import StaffInvite

    _admin_only(request)
    if invites.cancel(request.user, get_object_or_404(StaffInvite, pk=pk)):
        messages.success(request, 'Convite cancelado: o link deixou de valer.')
    return redirect('user_admin_invites')


@login_required
@require_POST
@require_recent_auth
def invite_resend(request, pk):
    from . import invites
    from .models import StaffInvite

    _admin_only(request)
    invite = get_object_or_404(StaffInvite, pk=pk)
    try:
        invites.resend(request.user, invite, request)
    except user_admin.Refused as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'Convite enviado de novo para {invite.email}; o link anterior deixou de valer.')
    return redirect('user_admin_invites')


INVITE_MISSES, INVITE_PERIOD = 20, 3600  # links inválidos por IP, por hora


def invite_accept(request, token):
    """Página pública do link do convite."""
    from django.contrib.auth import login

    from core import throttle
    from core.http import client_ip

    from . import invites
    from .forms import InviteAcceptForm

    if request.user.is_authenticated:
        messages.info(request, 'Saia da conta atual para aceitar o convite com uma conta nova.')
        return redirect('home')
    ip = client_ip(request) or '-'
    if throttle.blocked(INVITE_MISSES, INVITE_PERIOD, 'invite-miss', ip):
        return render(request, 'accounts/invite_accept.html', {'invalid': True}, status=429)
    invite = invites.find(token)
    if invite is None:
        throttle.hit(INVITE_MISSES, INVITE_PERIOD, 'invite-miss', ip)
        return render(request, 'accounts/invite_accept.html', {'invalid': True}, status=404)
    form = InviteAcceptForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            user = invites.accept(invite, form)
        except user_admin.Refused as exc:
            messages.error(request, str(exc))
            return redirect('login')
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, 'Conta criada. Agora cadastre o aplicativo autenticador.')
        return redirect('home')
    return render(request, 'accounts/invite_accept.html', {'form': form, 'invite': invite})
