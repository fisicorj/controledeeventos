import json
import logging

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMessage, get_connection
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.reauth import require_recent_auth
from certificates.models import Certificate
from certificates.pdf import certificate_pdf

from .forms import EmailSettingsForm, EmailTestForm, SiteSettingsForm
from .mail_html import with_html
from .models import EmailSettings, OutgoingEmail, SiteSettings
from .permissions import is_platform_admin
from .text import single_line

csp_logger = logging.getLogger('arena.csp')


def privacy(request):
    """Aviso de privacidade (LGPD), público."""
    return render(request, 'core/privacy.html', {'outbox_retention_days': django_settings.OUTBOX_RETENTION_DAYS})


@login_required
def settings_edit(request):
    if not is_platform_admin(request.user):
        raise PermissionDenied('Acesso restrito a administradores.')
    site = SiteSettings.load(fresh=True)
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
        raise PermissionDenied('Acesso restrito a administradores.')
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
@require_recent_auth
def email_settings(request):
    if not is_platform_admin(request.user):
        raise PermissionDenied('Acesso restrito a administradores.')
    config = EmailSettings.load(fresh=True)
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
            'queue': _queue_summary(),
            'default_ports_json': json.dumps({str(k): v for k, v in EmailSettings.DEFAULT_PORTS.items()}),
        },
    )


def _queue_summary():
    from . import outbox

    Status = OutgoingEmail.Status
    day_ago = timezone.now() - timezone.timedelta(hours=24)
    return {
        'pending': OutgoingEmail.objects.filter(status__in=[Status.PENDING, Status.SENDING]).count(),
        'failed': OutgoingEmail.objects.filter(status=Status.FAILED).count(),
        'retryable': outbox.retryable_failures().count(),
        'retry_days': django_settings.OUTBOX_RETRY_DAYS,
        'sent_today': OutgoingEmail.objects.filter(status=Status.SENT, sent_at__gte=day_ago).count(),
        'retrying': OutgoingEmail.objects.filter(status=Status.PENDING, attempts__gt=0).count(),
        'recent_failures': OutgoingEmail.objects.filter(status=Status.FAILED).order_by('-id')[:10],
    }


@login_required
def outbox_retry(request):
    """Devolve para a fila os e-mails que falharam (ex.: depois de corrigir a configuração do servidor)."""
    from . import outbox

    if not is_platform_admin(request.user):
        raise PermissionDenied('Acesso restrito a administradores.')
    if request.method == 'POST':
        count = outbox.retry_failed()
        if count:
            messages.success(request, f'{count} e-mail{"s" if count != 1 else ""} de volta à fila de envio.')
        else:
            messages.info(
                request,
                f'Não havia e-mails com falha nos últimos {django_settings.OUTBOX_RETRY_DAYS} dias. '
                'Falhas mais antigas não são reenviadas: o aviso chegaria fora de hora.',
            )
    return redirect('email_settings')


@login_required
@require_recent_auth
def email_test(request):
    """Envia um e-mail de teste com a configuração salva e mostra o resultado (inclusive o erro do servidor)."""
    if not is_platform_admin(request.user):
        raise PermissionDenied('Acesso restrito a administradores.')
    if request.method != 'POST':
        return redirect('email_settings')
    form = EmailTestForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Informe um e-mail válido para o teste.')
        return redirect('email_settings')
    config = EmailSettings.load()
    institution = SiteSettings.load().institution_name
    message = with_html(
        EmailMessage(
            f'{institution}: e-mail de teste',
            f'Este é um e-mail de teste enviado pela plataforma {institution} em '
            f'{timezone.localtime().strftime("%d/%m/%Y às %H:%M")}.\n\n'
            f'Servidor: {config.host or "(nenhum)"}:{config.port}\n'
            f'Se você recebeu esta mensagem, o envio de e-mails está funcionando.',
            to=[form.cleaned_data['recipient']],
        )
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


PRIVATE_IMAGES = {'assinatura': 'signature_image', 'fundo': 'certificate_background'}


@login_required
def private_image(request, field):
    """Mostra ao administrador a assinatura e a arte de fundo, que não ficam públicas em /media/."""
    import mimetypes

    from django.http import FileResponse, Http404

    if not is_platform_admin(request.user):
        raise PermissionDenied('Acesso restrito a administradores.')
    if field not in PRIVATE_IMAGES:
        raise Http404
    image = getattr(SiteSettings.load(), PRIVATE_IMAGES[field])
    if not image:
        raise Http404
    try:
        handle = image.open('rb')
    except FileNotFoundError as exc:
        raise Http404 from exc
    content_type = mimetypes.guess_type(image.name)[0] or 'application/octet-stream'
    if not content_type.startswith('image/') or content_type == 'image/svg+xml':
        content_type = 'application/octet-stream'
    response = FileResponse(handle, content_type=content_type)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


# ---------- Relatório de violações da Content-Security-Policy (SEC-07)

CSP_REPORTS_PER_IP, CSP_REPORT_PERIOD = 30, 3600


@csrf_exempt
@require_POST
def csp_report(request):
    """O navegador avisa aqui quando algo foi bloqueado (ou seria, no modo "report"). Vai para o log como aviso.

    Público e sem CSRF por natureza (quem envia é o navegador). Limitado por IP e por tamanho para não lotar o log."""
    from . import throttle
    from .http import client_ip

    if len(request.body) > 10_000 or not throttle.hit(CSP_REPORTS_PER_IP, CSP_REPORT_PERIOD, 'csp', client_ip(request)):
        return HttpResponse(status=204)
    try:
        report = json.loads(request.body).get('csp-report', {})
    except (ValueError, AttributeError):
        return HttpResponse(status=204)
    if isinstance(report, dict):
        csp_logger.warning(
            'CSP bloqueou %s em %s (diretiva %s).',
            # Quebras de linha saem: o relatório vem de qualquer pessoa e não pode forjar linhas no log (SEC-17).
            single_line(report.get('blocked-uri', ''))[:200],
            single_line(report.get('document-uri', ''))[:200],
            single_line(report.get('violated-directive') or report.get('effective-directive', ''))[:100],
        )
    return HttpResponse(status=204)


# ---------- Manutenção: faxina diária e exportação da auditoria

STALE_AFTER = timezone.timedelta(hours=36)  # faxina diária sem rodar há mais que isso: o worker/agendamento parou


@login_required
def maintenance_status(request):
    from django.conf import settings

    from . import audit_export
    from .models import MaintenanceRun

    if not is_platform_admin(request.user):
        raise PermissionDenied('Acesso restrito a administradores.')
    runs = list(MaintenanceRun.objects.all()[:10])
    last = next((run for run in runs if run.finished_at), None)
    folder = settings.AUDIT_EXPORT_DIR
    return render(
        request,
        'core/maintenance.html',
        {
            'runs': runs,
            'last': last,
            'stale': last is None or last.started_at < timezone.now() - STALE_AFTER,
            'export_folder': folder,
            'export_status': audit_export.status(folder) if folder else [],
            'anchor_email': settings.AUDIT_EXPORT_EMAIL or EmailSettings.load().error_alert_email,
        },
    )


@login_required
@require_POST
@require_recent_auth
def maintenance_run(request):
    from . import maintenance

    if not is_platform_admin(request.user):
        raise PermissionDenied('Acesso restrito a administradores.')
    results = maintenance.run(source=f'manual ({request.user.username})')
    failed = [label for label, value in results.items() if str(value).startswith(maintenance.FAILED_PREFIX)]
    if failed:
        messages.error(request, 'Faxina concluída com falha em: ' + ', '.join(failed) + '.')
    else:
        messages.success(request, 'Faxina concluída.')
    return redirect('maintenance_status')
