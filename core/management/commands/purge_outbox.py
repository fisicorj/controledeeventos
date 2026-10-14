from django.conf import settings
from django.core.management.base import BaseCommand

from core import outbox


class Command(BaseCommand):
    help = (
        'Apaga destinatário e conteúdo dos e-mails enviados há mais de OUTBOX_RETENTION_DAYS dias (padrão 30), '
        'mantendo só a contagem dos envios. O serviço worker já faz isso sozinho; sem ele, agende uma vez por dia.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=None, help='Prazo em dias (padrão: OUTBOX_RETENTION_DAYS).')

    def handle(self, *args, dias, **options):
        days = settings.OUTBOX_RETENTION_DAYS if dias is None else dias
        if days <= 0:
            self.stdout.write('Limpeza desligada (prazo 0).')
            return
        count = outbox.redact_sent(days)
        self.stdout.write(f'E-mails enviados há mais de {days} dia(s) com o conteúdo apagado: {count}.')
