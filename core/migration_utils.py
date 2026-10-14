"""Ajudantes para migrações."""

import sys

from django.db import migrations


def check_constraint_not_valid(app_label, model_name, constraint, describe_invalid):
    """AddConstraint que não quebra o deploy por causa de registros antigos.

    No PostgreSQL a regra é criada com NOT VALID: vale para tudo que for gravado ou alterado daqui em diante,
    sem conferir o que já existe. Os registros antigos que violam a regra são listados no terminal para correção
    (ao editar um deles, o banco exige datas corretas). Em outros bancos, cria a regra normalmente.
    `describe_invalid(Model)` devolve as linhas que violam a regra, já formatadas.
    """

    def forwards(apps, schema_editor):
        model = apps.get_model(app_label, model_name)
        if schema_editor.connection.vendor != 'postgresql':
            schema_editor.add_constraint(model, constraint)
            return
        invalid = describe_invalid(model)
        if invalid:
            sys.stdout.write(
                f'\n  Aviso: {len(invalid)} registro(s) com datas que a regra "{constraint.name}" não permite. '
                'Corrija pelo /admin/:\n' + ''.join(f'    {line}\n' for line in invalid)
            )
        table = schema_editor.quote_name(model._meta.db_table)
        check = constraint._get_check_sql(model, schema_editor)
        schema_editor.execute(
            f'ALTER TABLE {table} ADD CONSTRAINT {schema_editor.quote_name(constraint.name)} CHECK ({check}) NOT VALID'
        )

    def backwards(apps, schema_editor):
        model = apps.get_model(app_label, model_name)
        schema_editor.remove_constraint(model, constraint)

    return migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunPython(forwards, backwards)],
        state_operations=[migrations.AddConstraint(model_name=model_name, constraint=constraint)],
    )
