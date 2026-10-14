"""Alerta por e-mail quando o portal dá erro 500.

O destinatário é configurado em Configurações > E-mail ("E-mail para alertas de erro"). O alerta vai pela fila de
e-mails (não atrasa a página de erro) e tem limite de 10 por hora, para uma sequência de erros não lotar a caixa.
"""

import logging
import traceback

from django.conf import settings
from django.utils import timezone

ALERTS_PER_HOUR = 10


class ErrorAlertHandler(logging.Handler):
    def emit(self, record):
        if settings.DEBUG:
            return
        try:
            from . import outbox, throttle
            from .models import EmailSettings, SiteSettings

            to = EmailSettings.load().error_alert_email
            if not to or not throttle.hit(ALERTS_PER_HOUR, 3600, 'error-alert'):
                return
            request = getattr(record, 'request', None)
            lines = [
                f'Quando: {timezone.localtime().strftime("%d/%m/%Y %H:%M:%S")}',
                f'Mensagem: {record.getMessage()}',
            ]
            if request is not None:
                user = getattr(request, 'user', None)
                lines += [
                    f'Endereço: {request.method} {request.get_full_path()}',
                    f'Usuário: {user.username if user is not None and user.is_authenticated else "(não logado)"}',
                ]
            if record.exc_info:
                lines += ['', ''.join(traceback.format_exception(*record.exc_info))]
            portal = SiteSettings.load().portal_name
            outbox.enqueue(to, f'{portal}: erro no servidor', '\n'.join(lines), group='error-alert')
        except Exception:  # noqa: S110 o alerta nunca pode derrubar nada; o erro original já foi para o log
            pass  # (registrar aqui chamaria este mesmo handler de novo)
