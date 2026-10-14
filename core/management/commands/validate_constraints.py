"""Valida as regras do banco criadas com NOT VALID (BD-04).

As regras de data entram com NOT VALID (core/migration_utils.py) para não travar o deploy por causa de registros
antigos. Depois de corrigir esses registros, este comando confere a tabela inteira e marca a regra como válida.
Precisa rodar com o dono das tabelas (DB_MIGRATE_USER, quando configurado)."""

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

SAMPLE = 20  # quantos ids de registros problemáticos mostrar por regra

PENDING_SQL = """
    SELECT rel.relname, con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
    WHERE con.contype = 'c' AND NOT con.convalidated AND nsp.nspname = current_schema()
    ORDER BY rel.relname, con.conname
"""


def pending():
    """[(tabela, regra, expressão)] das regras CHECK ainda não validadas."""
    with connection.cursor() as cursor:
        cursor.execute(PENDING_SQL)
        rows = cursor.fetchall()
    result = []
    for table, name, definition in rows:
        expression = definition.removeprefix('CHECK ').removesuffix(' NOT VALID')
        result.append((table, name, expression))
    return result


def violations(table, expression):
    """Ids dos registros que violam a regra (no máximo SAMPLE) e o total."""
    quoted = connection.ops.quote_name(table)
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT count(*) FROM {quoted} WHERE NOT {expression}')  # noqa: S608 vem do catálogo
        total = cursor.fetchone()[0]
        cursor.execute(f'SELECT id FROM {quoted} WHERE NOT {expression} ORDER BY id LIMIT {SAMPLE}')  # noqa: S608
        ids = [row[0] for row in cursor.fetchall()]
    return total, ids


class Command(BaseCommand):
    help = (
        'Confere as regras do banco criadas com NOT VALID (datas de atividades e certificados) e, se nenhum registro '
        'antigo as viola, marca como válidas. Lista o que ainda impede a validação.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--conferir', action='store_true', help='Só lista, sem validar.')

    def handle(self, *args, conferir, **options):
        if connection.vendor != 'postgresql':
            self.stdout.write('Só faz sentido no PostgreSQL.')
            return
        rules = pending()
        if not rules:
            self.stdout.write(self.style.SUCCESS('Todas as regras já estão validadas.'))
            return
        blocked = 0
        for table, name, expression in rules:
            total, ids = violations(table, expression)
            if total:
                blocked += 1
                shown = ', '.join(str(i) for i in ids) + (' ...' if total > len(ids) else '')
                message = f'{table}.{name}: {total} registro(s) violam a regra (id {shown}). Corrija pelo /admin/.'
                self.stdout.write(self.style.WARNING(message))
                continue
            if conferir:
                self.stdout.write(f'{table}.{name}: pronta para validar.')
                continue
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    q = connection.ops.quote_name
                    cursor.execute(f'ALTER TABLE {q(table)} VALIDATE CONSTRAINT {q(name)}')
            except DatabaseError as exc:
                message = f'{table}.{name}: não foi possível validar ({exc}). Rode com o dono das tabelas.'
                raise CommandError(message) from exc
            self.stdout.write(self.style.SUCCESS(f'{table}.{name}: validada.'))
        if blocked:
            raise CommandError(f'{blocked} regra(s) ainda não podem ser validadas; corrija os registros listados.')
