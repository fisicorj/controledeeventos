from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetView,
)
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone

from core.models import SiteSettings

from . import mfa, verification
from .documents import is_cpf
from .forms import CodeForm, ProfileForm, SignUpForm, ThrottledAuthenticationForm
from .models import User, email_taken, release_email
from .privacy import can_delete_self

RESETS_PER_EMAIL, RESETS_PER_IP, RESET_PERIOD = 3, 30, 3600  # por IP é alto: laboratório usa um IP só


class PortalPasswordResetForm(PasswordResetForm):
    """O link vai para a conta confirmada com o e-mail; só se não houver nenhuma, para as não confirmadas
    (um cadastro sem confirmar com o e-mail de outra pessoa não recebe link na caixa dela)."""

    def get_users(self, email):
        users = list(super().get_users(email))
        verified = [u for u in users if u.email_verified]
        return iter(verified or users)


class PortalPasswordResetView(PasswordResetView):
    """Recuperação de senha com o nome do portal (definido em Configurações) no e-mail.

    Limite: 3 pedidos por hora para o mesmo e-mail e 30 por IP. Passando disso, a tela é a mesma
    ("se o e-mail existir, enviamos"), mas nada é enviado: não vira forma de lotar a caixa de alguém."""

    def form_valid(self, form):
        from django.http import HttpResponseRedirect

        from core import throttle
        from core.http import client_ip

        email = form.cleaned_data['email']
        ip = client_ip(self.request) or '-'
        within = throttle.hit(RESETS_PER_EMAIL, RESET_PERIOD, 'reset-email', email)
        within = throttle.hit(RESETS_PER_IP, RESET_PERIOD, 'reset-ip', ip) and within
        if not within:
            return HttpResponseRedirect(self.get_success_url())
        self.extra_email_context = {'portal_name': SiteSettings.load().portal_name}
        return super().form_valid(form)

    form_class = PortalPasswordResetForm


class PortalPasswordResetConfirmView(PasswordResetConfirmView):
    """Quem chega pelo link de nova senha provou que lê a caixa de entrada: o e-mail fica confirmado."""

    template_name = 'accounts/password_reset_confirm.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        if not self.user.email_verified and not email_taken(self.user.email, exclude_pk=self.user.pk):
            release_email(self.user.email, exclude_pk=self.user.pk)  # outra conta não confirmada com o mesmo e-mail
            User.objects.filter(pk=self.user.pk).update(email_verified=True)
        return response


class PortalLoginView(LoginView):
    template_name = 'accounts/login.html'
    authentication_form = ThrottledAuthenticationForm


class PortalLogoutView(LogoutView):
    """Ao sair, o navegador apaga o que o portal guardou no aparelho (dados offline do check-in, service worker).
    Vale em HTTPS; em HTTP o static/js/app.js faz a mesma limpeza antes de enviar o formulário."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        response['Clear-Site-Data'] = '"storage"'
        return response


def admin_logout(request):
    """Sair pelo /admin/ limpa o aparelho como o logout do portal (SEC-18): o painel não carrega o app.js."""
    from django.contrib import admin

    response = admin.site.logout(request)
    if request.method == 'POST':
        response['Clear-Site-Data'] = '"storage"'
    return response


SIGNUP_PERIOD = 3600


def _signup_key(request):
    from core.http import client_ip

    return ('signup-ip', client_ip(request) or '-')


def _signup_blocked(request):
    """O IP já criou SIGNUPS_PER_IP_HOUR contas na última hora? Contam só os cadastros feitos: um laboratório
    inteiro atrás do mesmo IP errando a senha não se bloqueia."""
    from core import throttle

    return throttle.blocked(settings.SIGNUPS_PER_IP_HOUR, SIGNUP_PERIOD, *_signup_key(request))


def _count_signup(request):
    from core import throttle

    throttle.hit(settings.SIGNUPS_PER_IP_HOUR, SIGNUP_PERIOD, *_signup_key(request))


def signup(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = SignUpForm(request.POST or None)
    if request.method == 'POST' and _signup_blocked(request):
        form.is_valid()
        form.add_error(
            None, 'Muitos cadastros feitos a partir desta rede na última hora. Espere um pouco e tente de novo.'
        )
    elif request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                # Conta antiga que nunca confirmou este e-mail sai do caminho, quando não tem histórico
                # (ver accounts.models.release_email). Se tiver, as duas convivem até uma delas confirmar.
                release_email(form.cleaned_data['email'])
                user = form.save(commit=False)
                user.email_verified = False
                user.privacy_accepted_at = timezone.now()
                user.save()
        except IntegrityError:  # o mesmo usuário de acesso cadastrado ao mesmo tempo por outra pessoa (QUA-03)
            form.add_error('username', 'Este nome de usuário acabou de ser escolhido por outra pessoa. Escolha outro.')
            context = {'form': form, 'domains': SiteSettings.load().student_domains()}
            return render(request, 'accounts/signup.html', context)
        _count_signup(request)
        # A resposta é a mesma se o e-mail já tiver conta confirmada: o código não sai, e o dono do e-mail
        # recebe um aviso (verification.send_code). Assim o cadastro não revela quem tem conta.
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        sent, error = verification.send_code(request, user, user.email)
        if sent:
            messages.success(request, f'Conta criada. Enviamos um código para {user.email}.')
        else:
            messages.error(request, error)
        return redirect('verify_email')
    return render(request, 'accounts/signup.html', {'form': form, 'domains': SiteSettings.load().student_domains()})


@login_required
def profile(request):
    user = request.user
    old_email = user.email
    form = ProfileForm(request.POST or None, instance=user)
    if form.is_valid():
        new_email = form.cleaned_data['email']
        changed = new_email.lower() != old_email.lower()
        new_document = form.cleaned_data['institutional_id']
        user = form.save(commit=False)
        user.email = old_email  # o e-mail novo (e a matrícula que depende dele) só vale depois de confirmado
        fields = ['first_name', 'last_name']
        if not changed:
            fields.append('institutional_id')
        else:
            user.institutional_id = User.objects.only('institutional_id').get(pk=user.pk).institutional_id
        user.save(update_fields=fields)
        if changed:
            sent, error = verification.send_code(request, user, new_email, institutional_id=new_document)
            if sent:
                messages.info(request, f'Enviamos um código para {new_email}. O e-mail só muda depois da confirmação.')
                return redirect('verify_email')
            messages.error(request, error)
            return redirect('profile')
        messages.success(request, 'Dados atualizados. Os próximos certificados já saem com o nome novo.')
        return redirect('profile')
    context = {
        'form': form,
        'domains': SiteSettings.load().student_domains(),
        'document_is_cpf': is_cpf(request.user.institutional_id),
        'can_delete': can_delete_self(request.user),
        'mfa_applies': mfa.applies_to(request.user),
        'mfa_active': mfa.device_for(request.user) is not None,
    }
    return render(request, 'accounts/profile.html', context)


@login_required
def verify_email(request):
    user = request.user
    entry = verification.pending(user)
    target = entry.email if entry else user.email
    if not entry and user.email_verified:
        return redirect('profile')
    form = CodeForm(request.POST if request.method == 'POST' and 'code' in request.POST else None)
    if request.method == 'POST' and request.POST.get('action') == 'resend':
        sent, error = verification.send_code(request, user, target)
        if sent:
            messages.success(request, f'Enviamos um novo código para {target}.')
        else:
            messages.error(request, error)
        return redirect('verify_email')
    if form.is_valid():
        ok, error = verification.confirm(user, form.cleaned_data['code'])
        if ok:
            text = 'E-mail confirmado.'
            if user.student_id_confirmed:
                text += f' Matrícula {user.institutional_id} confirmada.'
            messages.success(request, text)
            return redirect('my_registrations')
        form.add_error('code', error)
    return render(request, 'accounts/verify_email.html', {'form': form, 'target': target, 'has_code': bool(entry)})


class PortalPasswordChangeView(PasswordChangeView):
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('profile')

    def form_valid(self, form):
        messages.success(self.request, 'Senha alterada.')
        return super().form_valid(form)


@login_required
def my_data(request):
    """Cópia dos dados pessoais em JSON (LGPD, art. 18)."""
    import json

    from django.http import HttpResponse

    from .privacy import export_data

    content = json.dumps(export_data(request.user), ensure_ascii=False, indent=2)
    response = HttpResponse(content, content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="meus-dados-{request.user.username}.json"'
    return response


@login_required
def delete_account(request):
    """Exclusão da conta pelo próprio participante, com a senha como confirmação."""
    from django.contrib.auth import logout

    from core import throttle

    from .forms import LOGIN_FAILS_PER_USER_IP, LOGIN_PERIOD
    from .privacy import anonymize, can_delete_self

    user = request.user
    if not can_delete_self(user):
        messages.error(
            request,
            'Contas de organizador ou administrador não podem ser excluídas por aqui. Fale com um administrador.',
        )
        return redirect('profile')
    error = ''
    if request.method == 'POST':
        if throttle.blocked(LOGIN_FAILS_PER_USER_IP, LOGIN_PERIOD, 'delete-password', user.pk):
            error = 'Muitas tentativas com a senha errada. Espere 15 minutos.'
        elif request.POST.get('confirm', '').strip().upper() != 'EXCLUIR':
            error = 'Digite EXCLUIR para confirmar.'
        elif not user.check_password(request.POST.get('password', '')):
            throttle.hit(LOGIN_FAILS_PER_USER_IP, LOGIN_PERIOD, 'delete-password', user.pk)
            error = 'Senha incorreta.'
        else:
            cancelled = anonymize(user)
            logout(request)
            text = 'Sua conta foi excluída.'
            if cancelled:
                text += f' {cancelled} inscrição(ões) em atividades futuras foram canceladas.'
            messages.success(request, text)
            return redirect('home')
    upcoming = user.registrations.filter(
        status__in=['CONFIRMED', 'WAITLISTED'], activity__start_date__gt=timezone.now()
    ).count()
    return render(
        request,
        'accounts/delete_account.html',
        {'error': error, 'upcoming': upcoming},
    )
