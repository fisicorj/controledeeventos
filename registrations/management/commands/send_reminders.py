from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from activities.models import Activity
from registrations import notifications
from registrations.models import Registration


class Command(BaseCommand):
    help = (
        'Envia o lembrete de véspera para os inscritos confirmados em atividades que começam nas próximas '
        'HORAS horas (padrão 24). Cada inscrição recebe o lembrete uma única vez. Na atividade com escolha de '
        'sessões, o lembrete é de cada sessão escolhida (um e-mail por pessoa com as sessões da janela). '
        'Também lembra os avaliadores da chamada de trabalhos com avaliação pendente perto do prazo ou atrasada. '
        'Agende para rodar de hora em hora.'
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
                activity__per_session=False,  # por sessão: lembrete de cada sessão, abaixo
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
        session_sent, session_failed = self.session_reminders(now, horas, simular)
        sent, failed = sent + session_sent, failed + session_failed
        if not simular:
            self.stdout.write(self.style.SUCCESS(f'Lembretes enviados: {sent}. Falhas: {failed}.'))
            from submissions.services import remind_reviewers

            reminded = remind_reviewers()  # vão pela fila de e-mails
            if reminded:
                self.stdout.write(f'Avaliadores lembrados do prazo: {reminded}.')

    def session_reminders(self, now, horas, simular):
        """Atividade por sessão: um e-mail por pessoa com as sessões escolhidas que começam na janela."""
        from registrations.models import SessionEnrollment

        pending = (
            SessionEnrollment.objects.filter(
                status=SessionEnrollment.Status.CONFIRMED,
                reminder_sent_at__isnull=True,
                registration__status=Registration.Status.CONFIRMED,
                registration__activity__status=Activity.Status.PUBLISHED,
                session__start_at__gt=now,
                session__start_at__lte=now + timedelta(hours=horas),
            )
            .select_related('registration__user', 'registration__activity', 'session')
            .order_by('session__start_at')
        )
        by_registration = {}
        for enrollment in pending:
            by_registration.setdefault(enrollment.registration_id, []).append(enrollment)
        sent = failed = 0
        for enrollments in by_registration.values():
            registration = enrollments[0].registration
            label = f'{registration.user.email} ({registration.activity.title}: {len(enrollments)} sessão(ões))'
            if simular:
                self.stdout.write(f'[simulação] {label}')
                continue
            message = notifications.session_reminder_message(registration, [e.session for e in enrollments])
            if notifications.send_messages([message]):
                SessionEnrollment.objects.filter(pk__in=[e.pk for e in enrollments]).update(
                    reminder_sent_at=timezone.now()
                )
                sent += 1
            else:
                failed += 1
                self.stderr.write(f'Falha ao enviar para {label}')
        return sent, failed
