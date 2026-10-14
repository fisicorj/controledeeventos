"""BD-04: validar as regras criadas com NOT VALID."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase

from accounts.models import User
from activities.test_utils import make_activity, make_user
from core.management.commands.validate_constraints import pending


class ValidateConstraintsTests(TestCase):
    def test_valida_quando_nao_ha_registros_errados(self):
        self.assertTrue(any(name == 'activity_end_after_start' for _, name, _ in pending()))
        out = StringIO()
        call_command('validate_constraints', conferir=True, stdout=out)
        self.assertIn('pronta para validar', out.getvalue())
        self.assertTrue(pending())  # --conferir não muda nada
        call_command('validate_constraints', stdout=StringIO())
        self.assertEqual(pending(), [])
        out = StringIO()
        call_command('validate_constraints', stdout=out)
        self.assertIn('já estão validadas', out.getvalue())

    def test_lista_registros_antigos_que_impedem(self):
        activity = make_activity(make_user('org', User.Role.ORGANIZER))
        with connection.cursor() as cursor:
            # simula um registro de antes da regra: tira a regra, grava a data errada e recria como NOT VALID
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')  # o ALTER não roda com verificações de FK pendentes
            cursor.execute('ALTER TABLE activities_activity DROP CONSTRAINT activity_end_after_start')
            cursor.execute('UPDATE activities_activity SET end_date = start_date WHERE id = %s', [activity.pk])
            cursor.execute(
                'ALTER TABLE activities_activity ADD CONSTRAINT activity_end_after_start '
                'CHECK (end_date > start_date) NOT VALID'
            )
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command('validate_constraints', stdout=out)
        self.assertIn(f'activity_end_after_start: 1 registro(s) violam a regra (id {activity.pk})', out.getvalue())
        self.assertIn('activity_end_after_start', [name for _, name, _ in pending()])
