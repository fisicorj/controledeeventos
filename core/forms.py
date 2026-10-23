from django import forms

from .images import setup_image_fields
from .models import EmailSettings, SiteSettings


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = [
            'portal_name', 'primary_color', 'institution_name', 'city', 'logo',
            'signer_name', 'signer_role', 'signature_image',
            'certificate_background', 'certificate_title', 'certificate_opening', 'certificate_place',
            'document_label', 'certificate_offset_mm', 'certificate_show_qr', 'student_email_domains',
            'privacy_contact_email',
        ]  # fmt: skip
        widgets = {'primary_color': forms.TextInput(attrs={'type': 'color'})}

    def clean_student_email_domains(self):
        return _clean_domains(self.cleaned_data.get('student_email_domains', ''))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        setup_image_fields(self)


def _clean_domains(value):
    domains = [d.strip().lower().lstrip('@') for d in value.split(',') if d.strip()]
    for domain in domains:
        if '.' not in domain or ' ' in domain or '@' in domain:
            raise forms.ValidationError(f'"{domain}" não parece um domínio. Use algo como aluno.fsa.br.')
    return ', '.join(domains)


class EmailSettingsForm(forms.ModelForm):
    password = forms.CharField(
        label='Senha',
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
        help_text='Fica guardada criptografada. Em branco, mantém a senha atual.',
    )
    clear_password = forms.BooleanField(label='Remover a senha cadastrada', required=False)

    class Meta:
        model = EmailSettings
        fields = [
            'enabled', 'host', 'port', 'security', 'verify_certificate', 'username',
            'from_email', 'from_name', 'reply_to', 'timeout', 'error_alert_email',
        ]  # fmt: skip

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            elif name == 'security':
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'

    def clean(self):
        data = super().clean()
        username = data.get('username')
        has_new = bool(data.get('password'))
        keeps_old = self.instance.has_password and not data.get('clear_password')
        if username and not (has_new or keeps_old):
            self.add_error('password', 'Informe a senha do usuário do SMTP.')
        return data

    def save(self, commit=True):
        config = super().save(commit=False)
        if self.cleaned_data.get('clear_password') or not self.cleaned_data.get('username'):
            config.password_encrypted = ''
        if self.cleaned_data.get('password') and self.cleaned_data.get('username'):
            config.set_password(self.cleaned_data['password'])
        if commit:
            config.save()
        return config


class EmailTestForm(forms.Form):
    recipient = forms.EmailField(
        label='Enviar um e-mail de teste para', widget=forms.EmailInput(attrs={'class': 'form-control'})
    )
