from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignUpForm(UserCreationForm):
    first_name = forms.CharField(label='Nome', max_length=150)
    last_name = forms.CharField(label='Sobrenome', max_length=150)
    email = forms.EmailField(label='E-mail')
    institutional_id = forms.CharField(label='Matrícula/identificação institucional', required=False)

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'institutional_id', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean_email(self):
        # E-mail é guardado em minúsculas e comparado sem diferenciar maiúsculas,
        # para que "Ana@x.com" e "ana@x.com" não virem duas contas.
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Já existe uma conta com este e-mail.')
        return email
