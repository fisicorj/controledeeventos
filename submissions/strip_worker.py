"""Processo separado que tira os metadados de um trabalho (chamado por submissions/metadata.py).

Lê o arquivo pela entrada padrão e escreve o arquivo limpo na saída padrão. Roda fora do servidor web, com limite
de memória e de tempo: um arquivo feito para travar o leitor (PDF malformado, ZIP que descompacta em gigabytes)
derruba só este processo, nunca o site. Não usa o Django: só a biblioteca padrão e o pypdf.

Códigos de saída: 0 limpo; 3 formato sem limpeza; 4 grande demais depois de descompactado; outros, falha.
"""

import io
import re
import sys
import zipfile

# Defesa em profundidade: o processo principal já confere o índice do ZIP, mas o índice pode mentir.
MAX_PART = 25 * 1024 * 1024
MAX_TOTAL = 60 * 1024 * 1024
CHUNK = 1024 * 1024

DOCX_PATTERNS = [
    (re.compile(rb'<dc:creator>.*?</dc:creator>', re.S), b'<dc:creator></dc:creator>'),
    (re.compile(rb'<cp:lastModifiedBy>.*?</cp:lastModifiedBy>', re.S), b'<cp:lastModifiedBy></cp:lastModifiedBy>'),
    (re.compile(rb'<Company>.*?</Company>', re.S), b'<Company></Company>'),
    (re.compile(rb'<Manager>.*?</Manager>', re.S), b'<Manager></Manager>'),
    (re.compile(rb'w:author="[^"]*"'), b'w:author=""'),
    (re.compile(rb'w:initials="[^"]*"'), b'w:initials=""'),
]
ODT_PATTERNS = [
    (re.compile(rb'<meta:initial-creator>.*?</meta:initial-creator>', re.S), b''),
    (re.compile(rb'<dc:creator>.*?</dc:creator>', re.S), b''),
]


class TooLarge(Exception):
    pass


def clean_pdf(data):
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        return None
    writer = PdfWriter(clone_from=reader)
    writer.metadata = None
    writer._root_object.pop('/Metadata', None)  # XMP
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _read_limited(source, item, total):
    """Lê uma parte do ZIP em blocos, contando o que sai de verdade (e não o tamanho que o índice diz)."""
    parts, size = [], 0
    with source.open(item) as handle:
        while chunk := handle.read(CHUNK):
            size += len(chunk)
            if size > MAX_PART or total + size > MAX_TOTAL:
                raise TooLarge
            parts.append(chunk)
    return b''.join(parts), total + size


def clean_zip(data, wanted, patterns):
    source = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    total = 0
    with zipfile.ZipFile(out, 'w') as target:
        for item in source.infolist():
            content, total = _read_limited(source, item, total)
            if wanted(item.filename):
                for pattern, replacement in patterns:
                    content = pattern.sub(replacement, content)
            # mimetype do ODF precisa continuar o primeiro e sem compressão
            compress = zipfile.ZIP_STORED if item.filename == 'mimetype' else zipfile.ZIP_DEFLATED
            target.writestr(item, content, compress_type=compress)
    return out.getvalue()


def clean(extension, data):
    if extension == 'pdf':
        return clean_pdf(data)
    if extension == 'docx':
        return clean_zip(data, lambda n: n.startswith(('docProps/', 'word/')) and n.endswith('.xml'), DOCX_PATTERNS)
    if extension == 'odt':
        return clean_zip(data, lambda n: n == 'meta.xml', ODT_PATTERNS)
    return None


def _limit_memory(limit):
    """Teto de memória do próprio processo (Linux e macOS). No Windows, vale só o limite de tempo."""
    try:
        import resource
    except ImportError:
        return
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def main():
    extension = sys.argv[1] if len(sys.argv) > 1 else ''
    if len(sys.argv) > 2:
        _limit_memory(int(sys.argv[2]))
    data = sys.stdin.buffer.read()
    try:
        cleaned = clean(extension, data)
    except TooLarge:
        return 4
    if cleaned is None:
        return 3
    sys.stdout.buffer.write(cleaned)
    return 0


if __name__ == '__main__':
    sys.exit(main())
