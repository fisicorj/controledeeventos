"""Relatório de horas por turma (P3 da 4ª auditoria): para a coordenação conferir as atividades complementares.

Junta os certificados válidos das emissões avulsas que a pessoa pode gerenciar, da turma escolhida (a coluna
"Turma" da planilha) e, se quiser, de um semestre (pela data do certificado). Cada participante aparece uma vez,
reconhecido pela matrícula/CPF da planilha, ou pelo nome quando não há documento."""

from collections import OrderedDict

from .batch import batches_for
from .history import semester_label
from .models import Certificate


def groups_for(user):
    """Turmas que aparecem nas emissões que a pessoa pode ver, em ordem alfabética."""
    values = (
        Certificate.objects.filter(batch__in=batches_for(user), revoked_at__isnull=True)
        .exclude(participant_group='')
        .values_list('participant_group', flat=True)
        .distinct()
    )
    return sorted(set(values), key=str.lower)


def _key(certificate):
    return certificate.participant_document.strip().lower() or certificate.participant_name.strip().lower()


def _better_name(candidate, current):
    """Entre grafias do mesmo participante em planilhas diferentes, prefere a que não está toda em maiúsculas
    ("Ana Lima" em vez de "ANA LIMA")."""
    return current.isupper() and not candidate.isupper()


def report(user, group, semester=''):
    """[(participante, documento, [certificados], horas)], ordenado pelo nome, e a lista de semestres da turma."""
    certificates = (
        Certificate.objects.filter(
            batch__in=batches_for(user), batch__cancelled_at__isnull=True, revoked_at__isnull=True,
            participant_group__iexact=group,
        )
        .select_related('batch')
        .order_by('participant_name')
    )  # fmt: skip
    semesters = sorted({semester_label(c.certificate_date) for c in certificates}, reverse=True)
    names = {}  # a melhor grafia de cada pessoa, olhando todos os certificados (antes do filtro de semestre)
    for c in certificates:
        key = _key(c)
        if key not in names or _better_name(c.participant_name, names[key]):
            names[key] = c.participant_name
    people = OrderedDict()
    for c in certificates:
        if semester and semester_label(c.certificate_date) != semester:
            continue
        key = _key(c)
        entry = people.setdefault(key, {'name': names[key], 'document': c.participant_document, 'items': []})
        entry['items'].append(c)
    rows = [
        (p['name'], p['document'], p['items'], sum(c.workload_hours or 0 for c in p['items']))
        for p in sorted(people.values(), key=lambda p: p['name'].lower())
    ]
    return rows, semesters
