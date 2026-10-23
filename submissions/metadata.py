"""Avaliação às cegas: tira dos arquivos os metadados que costumam trazer o nome de quem escreveu.

- PDF: o dicionário de informações (autor, criador, título...) e o XMP;
- DOCX: autor e "última modificação por" (docProps/core.xml), empresa e gerente (docProps/app.xml) e o autor dos
  comentários e das alterações controladas;
- ODT: autor inicial e autor (meta.xml).
O texto do trabalho não é tocado: nomes no corpo do texto continuam por conta do autor. DOC (formato antigo) fica
como veio. Arquivo que não abre (corrompido, protegido por senha) também fica como veio.

Segurança: DOCX e ODT são arquivos ZIP, e um ZIP de poucos megabytes pode descompactar em gigabytes. Antes de
qualquer coisa, check_archive confere o índice do ZIP (sem descompactar) e recusa o que passa dos limites. A limpeza
roda num processo separado (strip_worker.py), com limite de memória e de tempo: se um arquivo travar o leitor, só
esse processo morre, e o trabalho fica guardado como veio.
"""

import logging
import subprocess
import sys
import zipfile
from pathlib import Path

from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

ZIP_FORMATS = {'docx', 'odt'}
MAX_ENTRIES = 2000
MAX_PART = 25 * 1024 * 1024  # mesmos limites do strip_worker
MAX_TOTAL = 60 * 1024 * 1024
WORKER = Path(__file__).with_name('strip_worker.py')
WORKER_TIMEOUT = 20  # segundos
WORKER_MEMORY = 512 * 1024 * 1024  # bytes de memória virtual do processo de limpeza (Linux)


class Rejected(Exception):
    """O arquivo não pode ser aceito; a mensagem explica por quê."""


REJECTED = (
    'Este arquivo ficaria grande demais depois de descompactado, ou está corrompido. Abra no editor, salve de novo '
    '(ou exporte em PDF) e envie outra vez.'
)


def _extension(name):
    return name.rsplit('.', 1)[-1].lower() if '.' in name else ''


def check_archive(uploaded):
    """DOCX e ODT: recusa ZIP com partes demais ou que descompacta além dos limites. Lê só o índice."""
    if _extension(uploaded.name) not in ZIP_FORMATS:
        return
    uploaded.seek(0)
    try:
        with zipfile.ZipFile(uploaded) as archive:
            items = archive.infolist()
    except (zipfile.BadZipFile, OSError):
        uploaded.seek(0)
        return  # não é ZIP: fica guardado como veio (o avaliador é que não vai conseguir abrir)
    uploaded.seek(0)
    if len(items) > MAX_ENTRIES:
        raise Rejected(REJECTED)
    if any(item.file_size > MAX_PART for item in items) or sum(item.file_size for item in items) > MAX_TOTAL:
        raise Rejected(REJECTED)


def strip(uploaded):
    """Devolve (arquivo sem os metadados, True) ou (o mesmo arquivo, False) quando não deu ou não se aplica.
    Levanta Rejected quando o arquivo descompacta além dos limites."""
    name = uploaded.name
    extension = _extension(name)
    if extension not in ZIP_FORMATS | {'pdf'}:
        return uploaded, False
    uploaded.seek(0)
    data = uploaded.read()
    uploaded.seek(0)
    try:
        result = subprocess.run(  # noqa: S603 comando fixo: o próprio Python e um script do projeto
            [sys.executable, '-I', str(WORKER), extension, str(WORKER_MEMORY)],
            input=data,
            capture_output=True,
            timeout=WORKER_TIMEOUT,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        logger.warning('Limpeza de metadados de %s passou do tempo ou não rodou; arquivo mantido.', name)
        return uploaded, False
    if result.returncode == 4:
        raise Rejected(REJECTED)
    if result.returncode != 0 or not result.stdout:
        if result.returncode != 3:
            logger.info('Limpeza de metadados de %s falhou (código %s); arquivo mantido.', name, result.returncode)
        return uploaded, False
    return ContentFile(result.stdout, name=name), True
