from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from activities.models import Activity
from registrations import notifications
from registrations.models import Registration


class Command(BaseCommand):
    help = (
        'Envia o lembrete de véspera para os inscritos confirmados em atividades que começam nas próximas '
        'HORAS horas (padrão 24). Cada inscrição recebe o lembrete uma única vez. Agende para rodar de hora em hora.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--horas', type=int, default=24, help='Janela de antecedência em horas (padrão 24).')
        parser.add_argument('--simular', action='store_true', help='Só mostra quem receberia, sem enviar.')

    def handle(self, *args, horas, simular, **options):
        now = timezone.now()
        pending = (
            Registration.objects.filter(
                status=Registration.Status.CONFIRMED,
                reminder_sent_at__isnull=True,
                offer_expires_at__isnull=True,  # vaga oferecida e ainda não confirmada não recebe o lembrete
                activity__status=Activity.Status.PUBLISHED,
                activity__start_date__gt=now,
                activity__start_date__lte=now + timedelta(hours=horas),
            )
            .select_related('user', 'activity')
            .order_by('activity__start_date')
        )
        sent = failed = 0
        for registration in pending:
            label = f'{registration.user.email} ({registration.activity.title})'
            if simular:
                self.stdout.write(f'[simulação] {label}')
                continue
            # Marca só depois de enviar com sucesso; quem falhar é tentado de novo na próxima execução.
            if notifications.send_messages([notifications.reminder_message(registration)]):
                Registration.objects.filter(pk=registration.pk).update(reminder_sent_at=timezone.now())
                sent += 1
            else:
                failed += 1
                self.stderr.write(f'Falha ao enviar para {label}')
        if not simular:
            self.stdout.write(self.style.SUCCESS(f'Lembretes enviados: {sent}. Falhas: {failed}.'))
