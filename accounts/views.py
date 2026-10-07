from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import PasswordResetView
from django.shortcuts import redirect, render

from core.models import SiteSettings

from .forms import SignUpForm


class PortalPasswordResetView(PasswordResetView):
    """Recuperação de senha com o nome do portal (definido em Configurações) no e-mail."""

    def form_valid(self, form):
        self.extra_email_context = {'portal_name': SiteSettings.load().portal_name}
        return super().form_valid(form)


def signup(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = SignUpForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, 'Cadastro realizado com sucesso.')
        return redirect('home')
    return render(request, 'accounts/signup.html', {'form': form})
