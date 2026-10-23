"""Ferramentas de operação do organizador, uma por módulo: duplicar edição, materiais pós-evento, crachás e
números da atividade. Os nomes usados nas URLs e em outros apps continuam disponíveis aqui."""

from .badges import badges, badges_pdf
from .duplication import activity_duplicate, duplicate_activity
from .institution import institution_panel
from .materials import (
    can_access_material,
    material_create,
    material_delete,
    material_edit,
    material_open,
    materials_for,
    participated,
)
from .numbers import activity_numbers, activity_stats, editions

__all__ = [
    'activity_duplicate',
    'activity_numbers',
    'activity_stats',
    'badges',
    'badges_pdf',
    'can_access_material',
    'duplicate_activity',
    'editions',
    'institution_panel',
    'material_create',
    'material_delete',
    'material_edit',
    'material_open',
    'materials_for',
    'participated',
]
