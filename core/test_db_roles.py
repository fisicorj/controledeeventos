"""SEC-03: o papel da aplicação no banco não muda a estrutura nem altera ou apaga a auditoria."""

import secrets
import unittest

import psycopg
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase, override_settings

from accounts.models import User
from activities.test_utils import make_user


def can_create_roles():
    with connection.cursor() as cursor:
        cursor.execute('SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user')
        return bool(cursor.fetchone()[0])


class DbRolesTests(TransactionTestCase):
    def setUp(self):
        if not can_create_roles():
            raise unittest.SkipTest('o usuário do banco dos testes não pode criar papéis (CREATEROLE)')
        self.role = f'arena_test_app_{secrets.token_hex(3)}'
        self.password = secrets.token_urlsafe(16)
        self.addCleanup(self.drop_role)

    def drop_role(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', [self.role])
            if cursor.fetchone() is None:
                return
            # No PostgreSQL 16, quem criou o papel precisa ser membro dele para apagar o que é dele.
            cursor.execute(f'GRANT {self.role} TO CURRENT_USER')
            cursor.execute(f'DROP OWNED BY {self.role}')
            cursor.execute(f'REVOKE {self.role} FROM CURRENT_USER')
            cursor.execute(f'DROP ROLE {self.role}')

    def connect_as_app(self):
        db = connection.settings_dict
        return psycopg.connect(
            dbname=db['NAME'], user=self.role, password=self.password, host=db['HOST'], port=db['PORT'],
            autocommit=True,
        )  # fmt: skip

    def setup_roles(self):
        with override_settings(DB_MIGRATE_USER=connection.settings_dict['USER']):
            call_command('setup_db_roles', app_user=self.role, app_password=self.password, stdout=None)

    def test_papel_da_aplicacao_tem_so_o_necessario(self):
        self.setup_roles()
        self.setup_roles()  # pode rodar de novo sem erro
        make_user('ana')
        with self.connect_as_app() as conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM accounts_user')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("UPDATE accounts_user SET first_name = 'Ana'")  # uso normal: pode
            cur.execute('SELECT count(*) FROM certificates_certificateevent')  # auditoria: lê
            denied = [
                'UPDATE certificates_certificateevent SET actor_name = %s',
                'DELETE FROM attendance_attendanceremoval',
                'DROP TRIGGER certificates_event_immutable ON certificates_certificateevent',
                'ALTER TABLE accounts_user ADD COLUMN x int',
                'CREATE TABLE intrusa (id int)',
                'DELETE FROM django_migrations',
            ]
            for statement in denied:
                with self.subTest(statement=statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(statement, ['x'] if '%s' in statement else None)

    def test_tabela_nova_ja_nasce_com_permissao(self):
        self.setup_roles()
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE tabela_nova_teste (id int)')
        try:
            with self.connect_as_app() as conn, conn.cursor() as cur:
                cur.execute('INSERT INTO tabela_nova_teste VALUES (1)')
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP TABLE tabela_nova_teste')

    def test_recusa_superusuario_e_mesmo_papel(self):
        from django.core.management.base import CommandError

        owner = connection.settings_dict['USER']
        with override_settings(DB_MIGRATE_USER=owner), self.assertRaises(CommandError):
            call_command('setup_db_roles', app_user=owner, app_password='x')
        with override_settings(DB_MIGRATE_USER=''), self.assertRaises(CommandError):
            call_command('setup_db_roles', app_user=self.role, app_password='x')
        call_command('setup_db_roles', if_configured=True)  # sem DB_MIGRATE_USER: não faz nada
        self.assertFalse(User.objects.exists())
