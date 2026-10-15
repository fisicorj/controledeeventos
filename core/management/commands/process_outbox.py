import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core import outbox

UPKEEP_EVERY = 300  # segundos: vagas com prazo vencido e retenção dos e-mails
REMINDERS_EVERY = 3600  # lembretes de véspera
MAINTENANCE_EVERY = 86400  # faxina diária (core/maintenance.py)


class Command(BaseCommand):
    help = (
        'Envia os e-mails da fila. Sem opções, envia o que estiver pendente e termina (para agendar de minuto '
        'em minuto). Com --continuo, fica rodando como serviço: confere a fila a cada INTERVALO segundos; a cada '
        '5 minutos devolve à fila de espera as vagas não confirmadas no prazo e apaga o conteúdo dos e-mails '
        'enviados há mais de OUTBOX_RETENTION_DAYS dias; de hora em hora envia os lembretes de véspera '
        '(desligue com --sem-lembretes se eles já estiverem agendados no cron); e uma vez por dia faz a faxina '
        '(run_maintenance). Também monta os ZIPs de lotes grandes, um por volta; com um serviço só para eles '
        '(--so-zips), use --sem-zips aqui para os e-mails não esperarem um ZIP de milhares de PDFs.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--continuo', action='store_true', help='Fica rodando (serviço do Docker, por exemplo).')
        parser.add_argument('--intervalo', type=int, default=15, help='Segundos entre as conferências (padrão 15).')
        parser.add_argument(
            '--sem-lembretes', action='store_true', help='Não envia os lembretes de véspera (já agendados no cron).'
        )
        parser.add_argument('--sem-zips', action='store_true', help='Não monta ZIPs (há um serviço --so-zips).')
        parser.add_argument('--so-zips', action='store_true', help='Só monta os ZIPs de lotes grandes.')

    def handle(self, *args, continuo, intervalo, sem_lembretes, sem_zips=False, so_zips=False, **options):
        if so_zips:
            return self.zips_only(continuo, intervalo)
        schedule = [(UPKEEP_EVERY, self.upkeep), (MAINTENANCE_EVERY, self.maintenance)]
        if not sem_lembretes:
            schedule.append((REMINDERS_EVERY, self.reminders))
        last_run = {task: float('-inf') for _, task in schedule}
        while True:
            if continuo:
                for every, task in schedule:
                    if time.monotonic() - last_run[task] >= every:
                        last_run[task] = time.monotonic()
                        task()
                close_old_connections()  # rodando por dias: renova a conexão com o banco se ela caiu
            self.requested_maintenance()
            if not sem_zips:
                self.zips(limit=1)  # um por volta: entre um ZIP e outro, os e-mails pendentes saem
            sent, failed = outbox.process()
            if sent or failed or not continuo:
                self.stdout.write(f'E-mails enviados: {sent}. Falhas: {failed}.')
            if not continuo:
                return
            time.sleep(max(intervalo, 1))

    def requested_maintenance(self):
        """Faxina pedida pelo botão "Rodar agora" em Configurações > Manutenção."""
        from core import maintenance

        try:
            maintenance.run_queued(log=self.stdout.write)
        except Exception as exc:  # o envio de e-mails não pode parar por causa disso
            self.stderr.write(f'Falha na faxina pedida pela tela: {exc}')

    def zips_only(self, continuo, intervalo):
        """Serviço só de ZIPs (DES-04 da 3ª auditoria): um lote de milhares de PDFs leva minutos e, no laço
        principal, seguraria confirmações de inscrição e códigos de verificação."""
        while True:
            if continuo:
                close_old_connections()
            self.zips()
            if not continuo:
                return
            time.sleep(max(intervalo, 1))

    def zips(self, limit=None):
        """ZIPs de lotes grandes pedidos na tela da emissão avulsa (certificates/zips.py)."""
        from certificates import zips

        try:
            built = zips.process(limit=limit)
        except Exception as exc:  # o envio de e-mails não pode parar por causa disso
            self.stderr.write(f'Falha ao gerar ZIP de certificados: {exc}')
        else:
            if built:
                self.stdout.write(f'ZIPs de certificados gerados: {built}.')

    def upkeep(self):
        """A cada 5 minutos: devolve à fila as vagas não confirmadas e apaga o conteúdo dos e-mails
        que passaram do prazo de retenção."""
        from registrations.services import expire_all_offers

        try:
            expired = expire_all_offers()
        except Exception as exc:  # o envio de e-mails não pode parar por causa disso
            self.stderr.write(f'Falha ao conferir os prazos da lista de espera: {exc}')
        else:
            if expired:
                self.stdout.write(f'Vagas com prazo vencido devolvidas à fila: {expired}.')
        try:
            redacted = outbox.redact_sent()
        except Exception as exc:
            self.stderr.write(f'Falha ao limpar e-mails antigos: {exc}')
        else:
            if redacted:
                self.stdout.write(f'E-mails antigos com o conteúdo apagado: {redacted}.')

    def reminders(self):
        """De hora em hora: lembrete de véspera (cada inscrição recebe uma vez só)."""
        from django.core.management import call_command

        try:
            call_command('send_reminders', stdout=self.stdout, stderr=self.stderr)
        except Exception as exc:
            self.stderr.write(f'Falha ao enviar os lembretes: {exc}')

    def maintenance(self):
        """Uma vez por dia: sessões vencidas, códigos e contadores antigos, cache de imagens."""
        from core import maintenance

        maintenance.run(log=self.stdout.write, source='worker')
