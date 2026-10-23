"""Inscrição em lote pela organização (O-02): uma lista de usuários ou e-mails confirmados, colada ou num CSV.

Primeiro a tela mostra o que vai acontecer com cada linha (inscrever, já inscrito, conta não encontrada, e-mail
não confirmado, conta desativada); só depois da confirmação as inscrições são feitas, uma a uma, pelo mesmo
caminho do "Incluir participante" (staff.add_participant), com e-mail e QR Code para cada pessoa.
"""

import csv
from dataclasses import dataclass

from django.db.models import Q
from django.db.models.functions import Lower

from accounts.models import User

from .models import Registration

MAX_ROWS = 500
MAX_FILE_BYTES = 1024 * 1024
HEADERS = {'email', 'e-mail', 'usuario', 'usuário', 'username', 'login'}

ADD, ALREADY, NOT_FOUND, UNVERIFIED, INACTIVE, REPEATED = (
    'add', 'already', 'not_found', 'unverified', 'inactive', 'repeated'
)  # fmt: skip
LABELS = {
    ADD: 'Vai ser inscrito',
    ALREADY: 'Já inscrito',
    NOT_FOUND: 'Conta não encontrada',
    UNVERIFIED: 'E-mail não confirmado',
    INACTIVE: 'Conta desativada',
    REPEATED: 'Repetido na lista',
}


class TooMany(Exception):
    pass


@dataclass
class Row:
    typed: str
    status: str
    user: User | None = None

    @property
    def label(self):
        return LABELS[self.status]


def read_file(upload):
    """Texto de um CSV (UTF-8 ou Latin-1, o que o Excel costuma salvar)."""
    raw = upload.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('O arquivo passa de 1 MB.')
    for encoding in ('utf-8-sig', 'latin-1'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError('Não consegui ler o arquivo.')  # pragma: no cover (latin-1 lê qualquer byte)


def parse(text):
    """Os identificadores, um por linha. No CSV com cabeçalho, usa a coluna de e-mail ou de usuário;
    sem cabeçalho, a primeira coluna preenchida. Aceita vírgula, ponto e vírgula ou tabulação."""
    lines = [line for line in (text or '').splitlines() if line.strip()]
    if not lines:
        return []
    first = lines[0]
    delimiter = '\t' if '\t' in first else ';' if ';' in first and ',' not in first else ','
    rows = [[cell.strip() for cell in row] for row in csv.reader(lines, delimiter=delimiter)]
    header = [cell.lower() for cell in rows[0]]
    column = next((index for index, name in enumerate(header) if name in HEADERS), None)
    if column is not None:
        rows = rows[1:]
    found = []
    for row in rows:
        cells = row if column is None else row[column : column + 1]
        value = next((cell for cell in cells if cell), '').strip('<> ')
        if value:
            found.append(value)
    if len(found) > MAX_ROWS:
        raise TooMany(f'A lista passa de {MAX_ROWS} pessoas. Divida em partes.')
    return found


def resolve(activity, typed_list):
    """O que acontece com cada linha, na ordem da lista."""
    lowered = {typed.lower() for typed in typed_list}
    users = list(
        User.objects.annotate(lower_username=Lower('username'), lower_email=Lower('email')).filter(
            Q(lower_username__in=lowered) | Q(lower_email__in=lowered)
        )
    )
    by_username = {user.username.lower(): user for user in users}
    by_email = {}
    for user in users:
        key = (user.email or '').lower()
        # Mais de uma conta com o mesmo e-mail: vale a confirmada (como no "Incluir participante").
        if key and (key not in by_email or (user.email_verified and not by_email[key].email_verified)):
            by_email[key] = user
    registered = set(
        Registration.objects.filter(
            activity=activity, status=Registration.Status.CONFIRMED, offer_expires_at__isnull=True
        ).values_list('user_id', flat=True)
    )
    seen, rows = set(), []
    for typed in typed_list:
        key = typed.lower()
        user = by_username.get(key) or by_email.get(key)
        if user is None:
            status = NOT_FOUND
        elif user.pk in seen:
            status = REPEATED
        elif not user.is_active:
            status = INACTIVE
        elif not user.email_verified:
            status = UNVERIFIED
        elif user.pk in registered:
            status = ALREADY
        else:
            status = ADD
        rows.append(Row(typed, status, user))
        if user is not None:
            seen.add(user.pk)
    return rows
