"""Backend de e-mail que usa a configuração cadastrada na plataforma (tela Configurações > E-mail).

É o EMAIL_BACKEND padrão do projeto. A configuração é lida do banco a cada conexão, então uma
alteração na tela vale para o próximo envio, sem reiniciar o servidor.
"""

import ssl

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
from django.utils.functional import cached_property


class ConfiguredSMTPBackend(SMTPBackend):
    def __init__(self, *args, verify_certificate=True, **kwargs):
        self.verify_certificate = verify_certificate
        super().__init__(*args, **kwargs)

    @cached_property
    def ssl_context(self):
        context = ssl.create_default_context()
        if not self.verify_certificate:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context


def build_backend(config, fail_silently=False):
    """Cria o backend real a partir de uma EmailSettings."""
    if not config.enabled:
        return ConsoleBackend(fail_silently=fail_silently)
    Security = config.Security
    return ConfiguredSMTPBackend(
        host=config.host,
        port=config.port,
        username=config.username or '',
        password=config.get_password() if config.username else '',
        use_tls=config.security == Security.STARTTLS,
        use_ssl=config.security == Security.SSL,
        timeout=config.timeout or None,
        verify_certificate=config.verify_certificate,
        fail_silently=fail_silently,
    )


def apply_sender(messages, config):
    """Mensagens sem remetente explícito saem com o remetente e o "responder para" configurados."""
    sender = config.sender()
    for message in messages:
        if sender and message.from_email in (None, '', settings.DEFAULT_FROM_EMAIL):
            message.from_email = sender
        if config.reply_to and not message.reply_to:
            message.reply_to = [config.reply_to]


class PlatformEmailBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self._backend = None
        self._config = None

    def _ensure_backend(self):
        if self._backend is None:
            from .models import EmailSettings

            self._config = EmailSettings.load()
            self._backend = build_backend(self._config, fail_silently=self.fail_silently)
        return self._backend

    def open(self):
        return self._ensure_backend().open()

    def close(self):
        if self._backend is not None:
            self._backend.close()

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        backend = self._ensure_backend()
        apply_sender(email_messages, self._config)
        return backend.send_messages(email_messages)
