"""Leitura da planilha de participantes da emissão avulsa (.xlsx ou .csv).

As colunas são reconhecidas pelo nome do cabeçalho, com folga: "Nome (conforme base de acesso)",
"Matrícula", "RA", "Turma", "E-mail"... A ordem não importa e colunas desconhecidas são ignoradas.
"""

import csv
import io
import re
import unicodedata
from dataclasses import asdict, dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

MAX_ROWS = 3000
MAX_FILE_SIZE = 5 * 1024 * 1024
HEADER_SCAN_ROWS = 10

# Conectivos que ficam em minúsculas no meio do nome
LOWER_WORDS = {'da', 'das', 'de', 'do', 'dos', 'e', 'di', 'du', 'del', 'della', 'van', 'von', 'y'}


class SpreadsheetError(Exception):
    """Problema que impede a leitura da planilha inteira (arquivo inválido, sem coluna de nome...)."""


@dataclass
class Row:
    line: int
    name: str
    original_name: str
    document: str = ''
    group: str = ''
    email: str = ''
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors

    def to_dict(self):
        return asdict(self)


def _plain(text):
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()


def column_kind(header):
    """Identifica a coluna pelo texto do cabeçalho."""
    h = _plain(header or '')
    if not h:
        return None
    words = h.split()
    if 'mail' in h:
        return 'email'
    if words[0] in ('turma', 'classe') or 'turma' in words:
        return 'group'
    if words[0] == 'nome' or h in ('participante', 'aluno', 'aluna', 'estudante'):
        return 'name'
    if words[0] in ('matricula', 'ra', 'rm', 'prontuario') or h.startswith('registro academico'):
        return 'document'
    return None


def cell_text(value):
    """Converte o valor da célula em texto: 12546456.0 vira "12546456", espaços repetidos somem."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r'\s+', ' ', str(value)).strip()


def smart_title(name):
    """Ajusta maiúsculas: "joaninha melo rego" -> "Joaninha Melo Rego", "MARIA DA SILVA" -> "Maria da Silva".
    Palavras com maiúsculas no meio (McDonald, DiCaprio) ficam como estão."""
    words = name.split(' ')
    out = []
    for i, word in enumerate(words):
        if not word:
            continue
        if not (word.islower() or word.isupper()):
            out.append(word)
            continue
        low = word.lower()
        if i > 0 and low in LOWER_WORDS:
            out.append(low)
        else:
            out.append('-'.join(part[:1].upper() + part[1:] for part in low.split('-')))
    return ' '.join(out)


def _read_xlsx(data):
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise SpreadsheetError('Não foi possível abrir a planilha. Confira se o arquivo é um .xlsx válido.') from exc
    try:
        sheet = workbook.worksheets[0]
        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(list(row))
            if len(rows) > MAX_ROWS + HEADER_SCAN_ROWS + 1:
                break
        return rows
    finally:
        workbook.close()


def _read_csv(data):
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SpreadsheetError('Não foi possível ler o CSV. Salve o arquivo em UTF-8.')
    first_line = text.split('\n', 1)[0]
    delimiter = max(';,\t', key=first_line.count)
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def read_table(uploaded):
    """Devolve as linhas do arquivo enviado como listas de valores."""
    if uploaded.size > MAX_FILE_SIZE:
        raise SpreadsheetError('O arquivo passa de 5 MB.')
    name = uploaded.name.lower()
    data = uploaded.read()
    if name.endswith('.xlsx'):
        return _read_xlsx(data)
    if name.endswith('.csv'):
        return _read_csv(data)
    if name.endswith('.xls'):
        raise SpreadsheetError('Arquivos .xls (Excel antigo) não são aceitos. No Excel, use "Salvar como" > .xlsx.')
    raise SpreadsheetError('Envie a planilha em .xlsx ou .csv.')


def _find_header(table):
    for index, row in enumerate(table[:HEADER_SCAN_ROWS]):
        columns = {}
        for col, value in enumerate(row):
            kind = column_kind(cell_text(value))
            if kind and kind not in columns:
                columns[kind] = col
        if 'name' in columns:
            return index, columns
    raise SpreadsheetError(
        'Não encontrei a coluna com o nome dos participantes. A primeira linha deve ter um cabeçalho '
        'como "Nome", "Matrícula", "Turma" e, se quiser enviar por e-mail, "E-mail".'
    )


def parse_participants(table, fix_case=True, existing=None):
    """Valida as linhas da planilha.

    existing: participantes já emitidos no lote, como conjunto de chaves (ver participant_key).
    Devolve (linhas, colunas encontradas).
    """
    existing = existing or set()
    header_index, columns = _find_header(table)

    def get(row, kind):
        col = columns.get(kind)
        return cell_text(row[col]) if col is not None and col < len(row) else ''

    rows, seen = [], {}
    for offset, raw in enumerate(table[header_index + 1 :], start=header_index + 2):
        values = [cell_text(v) for v in raw]
        if not any(values):
            continue
        original = get(raw, 'name')
        row = Row(
            line=offset,
            name=smart_title(original) if fix_case else original,
            original_name=original,
            document=get(raw, 'document'),
            group=get(raw, 'group'),
            email=get(raw, 'email').lower(),
        )
        if not row.name:
            row.errors.append('Sem nome')
        elif len(row.name) > 300:
            row.errors.append('Nome com mais de 300 caracteres')
        if len(row.document) > 50:
            row.errors.append('Matrícula com mais de 50 caracteres')
        if len(row.group) > 100:
            row.group = row.group[:100]
        if row.email:
            try:
                validate_email(row.email)
            except ValidationError:
                row.errors.append('E-mail inválido')
        if row.document and not any(ch.isdigit() for ch in row.document):
            # "Externo", "Convidado", "-", "N/A"...: não é uma matrícula. Não vai para o certificado
            # e não serve para achar repetidos (senão todos os externos virariam a mesma pessoa).
            if row.name:
                row.warnings.append(f'Sem matrícula ("{row.document}")')
            row.document = ''
        elif 'document' in columns and not row.document and row.name:
            row.warnings.append('Sem matrícula')

        key = participant_key(row.name, row.document)
        if row.name and key in existing:
            row.errors.append('Já tem certificado neste lote')
        elif row.name and key in seen:
            row.errors.append(f'Repetido (linha {seen[key]})')
        elif row.name:
            seen[key] = row.line
        rows.append(row)
        if len(rows) > MAX_ROWS:
            raise SpreadsheetError(f'A planilha passa de {MAX_ROWS} participantes. Divida em mais de um arquivo.')
    if not rows:
        raise SpreadsheetError('A planilha não tem participantes abaixo do cabeçalho.')
    return rows, set(columns)


def participant_key(name, document):
    """Identifica o participante dentro do lote: pela matrícula, ou pelo nome quando não há matrícula."""
    return ('doc', document.strip().upper()) if document.strip() else ('name', _plain(name))
