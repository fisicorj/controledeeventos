"""Cria (ou atualiza) o papel do banco usado pela aplicação, com só as permissões de que ela precisa (SEC-03)."""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

# Registros de auditoria: a aplicação só lê e inclui. O trigger também recusa UPDATE e DELETE, mas quem é dono
# da tabela pode apagar o trigger; a aplicação, sem ser dona, não pode.
APPEND_ONLY_TABLES = ['certificates_certificateevent', 'attendance_attendanceremoval', 'accounts_userchange']
READ_ONLY_TABLES = ['django_migrations']


class Command(BaseCommand):
    help = (
        'Cria ou atualiza o papel do banco da aplicação (DB_USER/DB_PASSWORD), sem permissão de mudar a estrutura, '
        'sem UPDATE/DELETE na auditoria e sem ser dono das tabelas. Roda com o papel dono (DB_MIGRATE_USER), '
        'depois do migrate. Pode rodar quantas vezes quiser.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--app-user', default=None, help='Papel da aplicação (padrão: DB_USER).')
        parser.add_argument('--app-password', default=None, help='Senha do papel (padrão: DB_PASSWORD).')
        parser.add_argument(
            '--if-configured', action='store_true', help='Sem DB_MIGRATE_USER, não faz nada (uso no Dockerfile).'
        )

    def handle(self, *args, app_user, app_password, if_configured, **options):
        app_user = app_user or os.getenv('DB_USER', '').strip()
        app_password = app_password if app_password is not None else os.getenv('DB_PASSWORD', '')
        if not settings.DB_MIGRATE_USER:
            if if_configured:
                return
            raise CommandError('Defina DB_MIGRATE_USER (dono das tabelas) e DB_MIGRATE_PASSWORD no .env.')
        owner = connection.settings_dict['USER']
        if not app_user or app_user == owner:
            raise CommandError('DB_USER precisa ser um papel diferente do DB_MIGRATE_USER.')
        if not app_password:
            raise CommandError('Defina a senha do papel da aplicação (DB_PASSWORD).')
        quote = connection.ops.quote_name
        app, owner_q, database = quote(app_user), quote(owner), quote(connection.settings_dict['NAME'])
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls '
                'FROM pg_roles WHERE rolname = %s',
                [app_user],
            )
            row = cursor.fetchone()
            if row and row[0]:
                raise CommandError(
                    f'O papel {app_user} tem poderes de administração no banco (superusuário, criar papéis ou bancos); '
                    'a aplicação precisa de um papel comum.'
                )
            # Papel novo nasce sem nenhum poder de administração (é o padrão do CREATE ROLE).
            verb = 'ALTER' if row else 'CREATE'
            try:
                with transaction.atomic():
                    cursor.execute(f'{verb} ROLE {app} WITH LOGIN PASSWORD %s', [app_password])
            except DatabaseError as exc:
                if verb == 'CREATE':
                    raise CommandError(
                        f'{owner} não pode criar papéis. Crie {app_user} como superusuário '
                        f"(CREATE ROLE {app_user} LOGIN PASSWORD '...';) e rode este comando de novo."
                    ) from exc
                # Papel criado por outra pessoa: as permissões abaixo ainda são aplicadas; a senha fica como está.
                self.stderr.write(f'Não foi possível atualizar a senha de {app_user} ({exc}); mantida a atual.')
            cursor.execute(f'GRANT CONNECT ON DATABASE {database} TO {app}')
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO {app}')
            cursor.execute(f'REVOKE CREATE ON SCHEMA public FROM {app}')
            cursor.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app}')
            cursor.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app}')
            for table in APPEND_ONLY_TABLES:
                cursor.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON {quote(table)} FROM {app}')
            for table in READ_ONLY_TABLES:
                cursor.execute(f'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {quote(table)} FROM {app}')
            # Tabelas criadas por migrações futuras já nascem com as mesmas permissões.
            cursor.execute(
                f'ALTER DEFAULT PRIVILEGES FOR ROLE {owner_q} IN SCHEMA public '
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app}'
            )
            cursor.execute(
                f'ALTER DEFAULT PRIVILEGES FOR ROLE {owner_q} IN SCHEMA public '
                f'GRANT USAGE, SELECT ON SEQUENCES TO {app}'
            )
            cursor.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = %s", [app_user])
            owned = cursor.fetchone()[0]
        self.stdout.write(
            f'Papel {app_user}: {"criado" if verb == "CREATE" else "atualizado"}, dono das tabelas: {owner}.'
        )
        if owned:
            self.stderr.write(
                f'Atenção: {owned} tabela(s) ainda pertencem a {app_user}. Transfira para {owner} com '
                f'"REASSIGN OWNED BY {app_user} TO {owner};" (como superusuário) e rode este comando de novo.'
            )
