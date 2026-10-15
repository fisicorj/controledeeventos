"""Telas da verificação em duas etapas (ver accounts/mfa.py)."""

import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core import crypto, throttle
from core.models import SiteSettings

from . import mfa

logger = logging.getLogger(__name__)

# Códigos errados em 15 minutos: 5 para a mesma conta vindo do mesmo IP, 20 para a conta de qualquer lugar.
# Quem tem só a senha e erra o código de casa não impede o dono de entrar de onde ele está (SEC-19).
ATTEMPTS, ATTEMPTS_PER_USER, PERIOD = 5, 20, 15 * 60
CODES_SESSION_KEY = 'mfa_new_codes'
UNREADABLE = (
    'Não foi possível ler o cadastro da verificação em duas etapas (a SECRET_KEY do servidor mudou?). '
    'Peça a um administrador para executar "python manage.py reset_mfa {username}" e cadastre de novo.'
)


class MfaCodeForm(forms.Form):
    code = forms.CharField(
        label='Código',
        max_length=20,
        widget=forms.TextInput(
            attrs={'inputmode': 'numeric', 'autocomplete': 'one-time-code', 'class': 'form-control code-input'}
        ),
    )


def _counters(request):
    from core.http import client_ip

    user, ip = request.user, client_ip(request) or '-'
    return [(ATTEMPTS, ('mfa', user.pk, ip)), (ATTEMPTS_PER_USER, ('mfa-user', user.pk))]


def _blocked(request):
    return any(throttle.blocked(limit, PERIOD, *key) for limit, key in _counters(request))


def _wrong(request):
    for limit, key in _counters(request):
        throttle.hit(limit, PERIOD, *key)


def _right(request):
    throttle.reset(*_counters(request)[0][1])


def _safe_next(request):
    target = request.POST.get('next') or request.GET.get('next') or ''
    allowed = url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure())
    return target if allowed else settings.LOGIN_REDIRECT_URL


@login_required
def mfa_verify(request):
    user = request.user
    if not mfa.applies_to(user) or mfa.device_for(user) is None:
        return redirect('home')
    if mfa.is_verified(request):
        return redirect(_safe_next(request))
    form = MfaCodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if _blocked(request):
            form.add_error('code', 'Muitas tentativas. Aguarde 15 minutos e tente de novo.')
        else:
            try:
                kind = mfa.verify(user, form.cleaned_data['code'])
            except crypto.SecretUnreadable:
                kind = None
                messages.error(request, UNREADABLE.format(username=user.username))
            if kind:
                _right(request)
                mfa.mark_verified(request)
                if kind == 'recovery':
                    left = len(mfa.device_for(user).recovery_codes)
                    messages.warning(
                        request,
                        f'Você entrou com um código de recuperação. Restam {left}. '
                        'Se perdeu o celular, reconfigure a verificação em duas etapas em "Meus dados".',
                    )
                return redirect(_safe_next(request))
            _wrong(request)
            logger.warning('Código de verificação em duas etapas incorreto para %s.', user.username)
            form.add_error('code', 'Código incorreto ou já usado.')
    return render(request, 'accounts/mfa_verify.html', {'form': form, 'next': _safe_next(request)})


@login_required
def mfa_setup(request):
    user = request.user
    if not mfa.applies_to(user):
        return redirect('profile')
    device = mfa.device_for(user)
    if device is not None:
        return render(
            request,
            'accounts/mfa_status.html',
            {'device': device, 'form': MfaCodeForm(), 'required': settings.MFA_REQUIRED and mfa.applies_to(user)},
        )
    pending = mfa.pending_device(user)
    try:
        secret = mfa.secret_of(pending)
    except crypto.SecretUnreadable:  # cadastro começado com outra SECRET_KEY: recomeça
        mfa.reset(user)
        pending = mfa.pending_device(user)
        secret = mfa.secret_of(pending)
    form = MfaCodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if _blocked(request):
            form.add_error('code', 'Muitas tentativas. Aguarde 15 minutos e tente de novo.')
        else:
            codes = mfa.confirm(pending, form.cleaned_data['code'])
            if codes:
                _right(request)
                mfa.mark_verified(request)
                request.session[CODES_SESSION_KEY] = codes
                logger.info('Verificação em duas etapas ativada para %s.', user.username)
                return redirect('mfa_codes')
            _wrong(request)
            form.add_error('code', 'Código incorreto. Confira o horário do celular e digite o código atual.')
    issuer = SiteSettings.load().portal_name or 'Arena+'
    uri = mfa.provisioning_uri(secret, user, issuer)
    context = {
        'form': form,
        'qr_svg': mfa.qr_svg(uri),
        'secret': ' '.join(secret[i : i + 4] for i in range(0, len(secret), 4)),
        'required': settings.MFA_REQUIRED and mfa.applies_to(user),
    }
    return render(request, 'accounts/mfa_setup.html', context)


@login_required
def mfa_codes(request):
    """Mostra os códigos de recuperação uma única vez, logo depois de gerados."""
    codes = request.session.pop(CODES_SESSION_KEY, None)
    if not codes:
        return redirect('mfa_setup')
    return render(request, 'accounts/mfa_codes.html', {'codes': codes})


def _confirm_action(request):
    """Ações sobre um cadastro ativo pedem um código atual: quem pegou a sessão aberta não consegue desligar."""
    user = request.user
    if mfa.device_for(user) is None:
        raise PermissionDenied('Verificação em duas etapas não está ativa.')
    form = MfaCodeForm(request.POST)
    if _blocked(request):
        messages.error(request, 'Muitas tentativas. Aguarde 15 minutos e tente de novo.')
        return False
    if form.is_valid() and mfa.verify_totp(user, form.cleaned_data['code']):
        _right(request)
        return True
    _wrong(request)
    messages.error(request, 'Código incorreto. Use o código atual do aplicativo.')
    return False


@login_required
@require_POST
def mfa_new_codes(request):
    if not _confirm_action(request):
        return redirect('mfa_setup')
    request.session[CODES_SESSION_KEY] = mfa.new_recovery_codes(mfa.device_for(request.user))
    return redirect('mfa_codes')


@login_required
@require_POST
def mfa_disable(request):
    if not _confirm_action(request):
        return redirect('mfa_setup')
    mfa.reset(request.user)
    request.session.pop(mfa.SESSION_KEY, None)  # com MFA obrigatório, o próximo passo é cadastrar de novo
    logger.info('Verificação em duas etapas desativada por %s.', request.user.username)
    if settings.MFA_REQUIRED and mfa.applies_to(request.user):
        messages.info(request, 'Cadastro anterior apagado. Cadastre o aplicativo de novo para continuar.')
    else:
        messages.success(request, 'Verificação em duas etapas desativada.')
    return redirect('mfa_setup')


class ReauthForm(forms.Form):
    password = forms.CharField(
        label='Sua senha',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'current-password'}),
    )
    code = forms.CharField(
        label='Código do aplicativo',
        max_length=20,
        required=False,
        widget=forms.TextInput(
            attrs={'inputmode': 'numeric', 'autocomplete': 'one-time-code', 'class': 'form-control code-input'}
        ),
    )


@login_required
def reauth_view(request):
    """Confirma a identidade antes de uma ação crítica (ver accounts/reauth.py)."""
    from . import reauth

    user = request.user
    uses_mfa = mfa.device_for(user) is not None
    form = ReauthForm(request.POST or None)
    if not uses_mfa:
        del form.fields['code']
    if request.method == 'POST' and form.is_valid():
        if _blocked(request):
            form.add_error(None, 'Muitas tentativas. Aguarde 15 minutos e tente de novo.')
        elif not user.check_password(form.cleaned_data['password']):
            _wrong(request)
            form.add_error('password', 'Senha incorreta.')
        elif uses_mfa and not mfa.verify_totp(user, form.cleaned_data.get('code') or ''):
            _wrong(request)
            form.add_error('code', 'Código incorreto ou já usado. Use o código atual do aplicativo.')
        else:
            _right(request)
            reauth.mark(request)
            return redirect(_safe_next(request))
    context = {'form': form, 'next': _safe_next(request), 'minutes': settings.REAUTH_MINUTES}
    return render(request, 'accounts/reauth.html', context)
