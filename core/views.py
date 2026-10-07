import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage, get_connection
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone

from certificates.models import Certificate
from certificates.pdf import certificate_pdf

from .forms import EmailSettingsForm, EmailTestForm, SiteSettingsForm
from .models import EmailSettings, SiteSettings
from .permissions import is_platform_admin


@login_required
def settings_edit(request):
    if not is_platform_admin(request.user):
        return HttpResponseForbidden('Acesso restrito a administradores.')
    site = SiteSettings.load()
    form = SiteSettingsForm(request.POST or None, request.FILES or None, instance=site)
    if form.is_valid():
        form.save()
        messages.success(request, 'Configurações salvas.')
        return redirect('site_settings')
    # 'portal' vem do context processor (versão salva), não do objeto alterado por um formulário inválido.
    return render(request, 'core/settings.html', {'form': form})


@login_required
def certificate_preview(request):
    """Certificado de exemplo com as configurações atuais (nada é gravado no banco)."""
    if not is_platform_admin(request.user):
        return HttpResponseForbidden('Acesso restrito a administradores.')
    now = timezone.now()
    sample = Certificate(
        code='EXEM-PLOD-EMO2',
        participant_name='Nome do Participante',
        participant_document='00000000',
        activity_title='Título da Atividade',
        activity_type='Evento',
        start_date=now,
        end_date=now + timezone.timedelta(hours=4),
        workload_hours=4,
        attendance_percent=100,
        issued_at=now,
    )
    response = HttpResponse(
        certificate_pdf(sample, signer_name=request.user.get_full_name() or 'Organizador'),
        content_type='application/pdf',
    )
    response['Content-Disposition'] = 'inline; filename="certificado-exemplo.pdf"'
    return response


@login_required
def email_settings(request):
    if not is_platform_admin(request.user):
        return HttpResponseForbidden('Acesso restrito a administradores.')
    config = EmailSettings.load()
    form = EmailSettingsForm(request.POST or None, instance=config)
    if form.is_valid():
        form.save()
        messages.success(
            request, 'Configuração de e-mail salva. Use o envio de teste para confirmar que está funcionando.'
        )
        return redirect('email_settings')
    return render(
        request,
        'core/email_settings.html',
        {
            'form': form,
            'config': config,
            'test_form': EmailTestForm(initial={'recipient': request.user.email}),
            'default_ports_json': json.dumps({str(k): v for k, v in EmailSettings.DEFAULT_PORTS.items()}),
        },
    )


@login_required
def email_test(request):
    """Envia um e-mail de teste com a configuração salva e mostra o resultado (inclusive o erro do servidor)."""
    if not is_platform_admin(request.user):
        return HttpResponseForbidden('Acesso restrito a administradores.')
    if request.method != 'POST':
        return redirect('email_settings')
    form = EmailTestForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Informe um e-mail válido para o teste.')
        return redirect('email_settings')
    config = EmailSettings.load()
    institution = SiteSettings.load().institution_name
    message = EmailMessage(
        f'{institution}: e-mail de teste',
        f'Este é um e-mail de teste enviado pela plataforma {institution} em '
        f'{timezone.localtime().strftime("%d/%m/%Y às %H:%M")}.\n\n'
        f'Servidor: {config.host or "(nenhum)"}:{config.port}\n'
        f'Se você recebeu esta mensagem, o envio de e-mails está funcionando.',
        to=[form.cleaned_data['recipient']],
    )
    try:
        with get_connection(fail_silently=False) as connection:
            connection.send_messages([message])
    except Exception as exc:  # o erro do servidor é mostrado ao administrador para diagnóstico
        detail = f'{type(exc).__name__}: {exc}'[:480]
        config.last_test_ok, config.last_test_message = False, detail
        messages.error(request, f'Falha no envio de teste. {detail}')
    else:
        config.last_test_ok = True
        if config.enabled:
            config.last_test_message = f'Enviado para {form.cleaned_data["recipient"]}.'
            messages.success(
                request,
                f'E-mail de teste enviado para {form.cleaned_data["recipient"]}. '
                'Confira a caixa de entrada (e o spam).',
            )
        else:
            config.last_test_message = 'Envio por SMTP desligado: a mensagem foi só registrada no log.'
            messages.info(request, 'O envio por SMTP está desligado: a mensagem foi só registrada no log.')
    config.last_test_at = timezone.now()
    config.save(update_fields=['last_test_ok', 'last_test_message', 'last_test_at'])
    return redirect('email_settings')
