from django.core.management.base import BaseCommand

from core import maintenance


class Command(BaseCommand):
    help = (
        'Faxina diária: apaga sessões vencidas, códigos de confirmação e contadores de tentativas antigos, cópias de '
        'imagens do PDF sem uso e o conteúdo dos e-mails enviados que passaram do prazo. O serviço worker já faz '
        'isso uma vez por dia; sem ele, agende este comando diariamente.'
    )

    def handle(self, *args, **options):
        maintenance.run(log=self.stdout.write)
