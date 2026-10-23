from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.urls import reverse
from django.utils.html import format_html

from core import throttle
from core.models import SiteSettings

from . import known_device
from .documents import clean_document
from .models import User, reserved_username

DOCUMENT_HELP = 'Aluno: a matrícula, igual à do e-mail institucional. Convidado: o CPF (opcional).'


class EmailUniqueByVerifiedMixin:
    """O e-mail só é único entre contas confirmadas, e o formulário não diz se um endereço já tem conta (SEC-09):
    quem decide é a confirmação por código (accounts/verification.py), que avisa o dono do e-mail. Por isso a
    checagem automática do e-mail (unicidade e a constraint unique_verified_email) fica de fora do formulário."""

    def _get_validation_exclusions(self):
        return set(super()._get_validation_exclusions()) | {'email'}


class SignUpForm(EmailUniqueByVerifiedMixin, UserCreationForm):
    first_name = forms.CharField(label='Nome', max_length=150)
    last_name = forms.CharField(label='Sobrenome', max_length=150)
    email = forms.EmailField(
        label='E-mail',
        help_text='Aluno da instituição: use o seu e-mail institucional. Vamos enviar um código para confirmar.',
    )
    institutional_id = forms.CharField(label='Matrícula ou CPF', max_length=50, required=False, help_text=DOCUMENT_HELP)
    accept_privacy = forms.BooleanField(
        label='Li e concordo com o aviso de privacidade',
        error_messages={'required': 'Para criar a conta, é preciso concordar com o aviso de privacidade.'},
    )

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'institutional_id', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        # Único texto de ajuda com HTML: marcado como seguro aqui (o template escapa todos os outros).
        self.fields['accept_privacy'].help_text = format_html(
            '<a href="{}" target="_blank">Leia o aviso</a>: quais dados guardamos, para quê e como pedir a exclusão.',
            reverse('privacy'),
        )

    def clean_username(self):
        username = super().clean_username()
        if reserved_username(username):
            raise forms.ValidationError('Este nome de usuário é reservado pelo sistema. Escolha outro.')
        return username

    def clean_email(self):
        # E-mail é guardado em minúsculas e comparado sem diferenciar maiúsculas,
        # para que "Ana@x.com" e "ana@x.com" não virem duas contas.
        return self.cleaned_data['email'].strip().lower()

    def clean_institutional_id(self):
        return _clean_document_field(self)


def _clean_document_field(form):
    value = form.cleaned_data.get('institutional_id', '')
    email = form.cleaned_data.get('email')
    if not email:  # o erro do e-mail já aparece; a matrícula é conferida quando ele estiver certo
        return (value or '').strip()
    return clean_document(value, email, SiteSettings.load())


class ProfileForm(EmailUniqueByVerifiedMixin, forms.ModelForm):
    """Dados que o próprio usuário pode corrigir. O nome aparece nos certificados."""

    first_name = forms.CharField(label='Nome', max_length=150)
    last_name = forms.CharField(label='Sobrenome', max_length=150)
    email = forms.EmailField(
        label='E-mail',
        help_text='Se trocar, enviamos um código para o endereço novo; a troca só vale depois da confirmação.',
    )
    institutional_id = forms.CharField(label='Matrícula ou CPF', max_length=50, required=False, help_text=DOCUMENT_HELP)

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'institutional_id')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()

    def clean_institutional_id(self):
        return _clean_document_field(self)


# Senhas erradas em 15 minutos (SEC-08):
# - 5 para o mesmo usuário vindo do mesmo IP: bloqueia só essa combinação. Quem tenta derrubar a conta de outra
#   pessoa a partir de casa não impede o dono de entrar de onde ele está;
# - 50 para o mesmo usuário vindo de qualquer IP: teto contra ataque distribuído. Não vale para o navegador em
#   que a pessoa já entrou com a senha certa (accounts/known_device.py): assim ninguém trava o login de outra
#   pessoa de propósito só sabendo o nome de usuário dela;
# - 100 por IP: alto porque num laboratório da faculdade todos os alunos saem pelo mesmo IP.
LOGIN_FAILS_PER_USER_IP, LOGIN_FAILS_PER_USER, LOGIN_FAILS_PER_IP, LOGIN_PERIOD = 5, 50, 100, 15 * 60
THROTTLED_LOGIN = 'Muitas tentativas de entrar. Espere 15 minutos ou use "Esqueceu a senha?".'


def _login_counters(username, ip):
    return [
        (LOGIN_FAILS_PER_USER_IP, ('login-user-ip', username.lower(), ip)),
        (LOGIN_FAILS_PER_USER, ('login-user', username.lower())),
        (LOGIN_FAILS_PER_IP, ('login-ip', ip)),
    ]


class LoginThrottleMixin:
    """Limite de senhas erradas (ver os números acima). Vale para o login do portal e o do /admin/."""

    def clean(self):
        from core.http import client_ip

        username = (self.data.get('username') or '').strip()
        ip = client_ip(self.request) if self.request else '-'
        counters = _login_counters(username, ip)
        known = known_device.is_known(self.request, username)
        checked = [(limit, key) for limit, key in counters if not (known and key[0] == 'login-user')]
        if any(throttle.blocked(limit, LOGIN_PERIOD, *key) for limit, key in checked):
            raise forms.ValidationError(self.error_messages['throttled'], code='throttled')
        try:
            data = super().clean()
        except forms.ValidationError:
            for limit, key in counters:
                throttle.hit(limit, LOGIN_PERIOD, *key)
            raise
        throttle.reset('login-user-ip', username.lower(), ip)
        known_device.remember(self.request, username)
        return data


class ThrottledAuthenticationForm(LoginThrottleMixin, AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': 'Usuário ou senha incorretos.',
        'throttled': THROTTLED_LOGIN,
    }


class ThrottledAdminAuthenticationForm(LoginThrottleMixin, AdminAuthenticationForm):
    error_messages = {**AdminAuthenticationForm.error_messages, 'throttled': THROTTLED_LOGIN}


class CodeForm(forms.Form):
    code = forms.CharField(
        label='Código de 6 dígitos',
        max_length=12,
        widget=forms.TextInput(
            attrs={'inputmode': 'numeric', 'autocomplete': 'one-time-code', 'class': 'form-control code-input'}
        ),
    )


class InviteAcceptForm(UserCreationForm):
    """Conta criada pelo convite de equipe (accounts/invites.py): o e-mail e o papel vêm do convite."""

    first_name = forms.CharField(label='Nome', max_length=150)
    last_name = forms.CharField(label='Sobrenome', max_length=150)
    accept_privacy = forms.BooleanField(
        label='Li e concordo com o aviso de privacidade',
        error_messages={'required': 'Para criar a conta, é preciso concordar com o aviso de privacidade.'},
    )

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != 'accept_privacy':
                field.widget.attrs['class'] = 'form-control'

    def clean_username(self):
        username = super().clean_username()
        if reserved_username(username):
            raise forms.ValidationError('Este nome de usuário é reservado pelo sistema. Escolha outro.')
        return username
