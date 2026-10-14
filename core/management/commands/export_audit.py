from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core import audit_export


class Command(BaseCommand):
    help = (
        'Exporta a auditoria de certificados e as presenças removidas para arquivos encadeados por hash '
        '(JSON Lines), continuando de onde a última exportação parou. Copie a pasta para um armazenamento que não '
        'permita alterar nem apagar. Com --conferir, verifica a cadeia dos arquivos já exportados.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--pasta', default=None, help='Pasta de destino (padrão: AUDIT_EXPORT_DIR do .env).')
        parser.add_argument('--conferir', action='store_true', help='Só confere os arquivos já exportados.')

    def handle(self, *args, pasta, conferir, **options):
        folder = pasta or settings.AUDIT_EXPORT_DIR
        if not folder:
            raise CommandError('Informe a pasta com --pasta ou defina AUDIT_EXPORT_DIR no .env.')
        failed = False
        for stream in audit_export.STREAMS if conferir else ():
            if conferir:
                checked, problems = audit_export.verify_chain(folder, stream)
                for problem in problems:
                    self.stderr.write(f'[{stream}] {problem}')
                failed = failed or bool(problems)
                status = 'com problemas' if problems else 'íntegra'
                self.stdout.write(f'{stream}: {checked} registro(s) conferido(s), cadeia {status}.')
        if not conferir:
            try:
                results = audit_export.export_all(folder)
            except audit_export.ExportBusy as exc:
                raise CommandError(str(exc)) from exc
            for stream, (path, count) in results.items():
                self.stdout.write(f'{stream}: {count} registro(s) novo(s)' + (f' em {path.name}.' if path else '.'))
        if failed:
            raise CommandError('A cadeia de algum arquivo exportado não confere.')
