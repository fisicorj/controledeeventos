from django.core.management.base import BaseCommand

from registrations.services import expire_all_offers


class Command(BaseCommand):
    help = (
        'Cancela as vagas oferecidas da lista de espera que não foram confirmadas no prazo e chama o próximo da '
        'fila. O serviço "worker" do Docker já faz isso; sem Docker, agende de hora em hora.'
    )

    def handle(self, *args, **options):
        total = expire_all_offers()
        self.stdout.write(f'Vagas com prazo vencido devolvidas à fila: {total}.')
