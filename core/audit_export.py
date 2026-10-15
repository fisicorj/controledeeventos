"""Exportação dos registros de auditoria para guardar fora do servidor (complementa a SEC-03).

Os registros (auditoria de certificados e presenças removidas) saem em arquivos JSON Lines, um por exportação,
encadeados por hash: cada linha leva o hash da anterior, e o hash de cada linha cobre o conteúdo dela e o da
anterior. Mudar, apagar ou reordenar qualquer linha, em qualquer arquivo, quebra a cadeia a partir dali
(verify_chain). Cada exportação continua de onde a anterior parou, lendo o último arquivo da pasta.

Para valer como prova, copie a pasta para um armazenamento que não permita alterar nem apagar (por exemplo, um
bucket com bloqueio de objetos, ou mídia de gravação única) e guarde fora do servidor.
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from django.utils import timezone

GENESIS = '0' * 64

STREAMS = {
    'certificados': ('certificates', 'CertificateEvent'),
    'presencas': ('attendance', 'AttendanceRemoval'),
    'contas': ('accounts', 'UserChange'),
}


def _serialize(obj):
    if isinstance(obj, datetime):
        return timezone.localtime(obj).isoformat()
    return str(obj)


def _record(row):
    data = {}
    for field in row._meta.concrete_fields:
        value = getattr(row, field.attname)
        data[field.attname] = value
    return data


def _line_hash(prev_hash, payload):
    return hashlib.sha256((prev_hash + payload).encode()).hexdigest()


def _files(folder, stream):
    return sorted(Path(folder).glob(f'auditoria-{stream}-*.jsonl'))


def last_state(folder, stream):
    """(último id exportado, último hash) a partir do arquivo mais recente da pasta."""
    files = _files(folder, stream)
    if not files:
        return 0, GENESIS
    last_line = ''
    with files[-1].open(encoding='utf-8') as handle:
        for line in handle:
            if line.strip():
                last_line = line
    if not last_line:
        return 0, GENESIS
    entry = json.loads(last_line)
    return entry['registro']['id'], entry['hash']


def export(folder, stream):
    """Exporta os registros novos de um tipo. Devolve (arquivo ou None, quantidade)."""
    from django.apps import apps

    model = apps.get_model(*STREAMS[stream])
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    last_id, prev_hash = last_state(folder, stream)
    rows = model.objects.filter(pk__gt=last_id).order_by('pk')
    if not rows.exists():
        return None, 0
    stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
    first = rows.first().pk
    target = folder / f'auditoria-{stream}-{first:010d}-{stamp}.jsonl'
    temp = target.with_suffix('.parcial')
    count = 0
    with temp.open('w', encoding='utf-8') as handle:
        for row in rows.iterator(chunk_size=2000):
            payload = json.dumps(_record(row), default=_serialize, ensure_ascii=False, sort_keys=True)
            line_hash = _line_hash(prev_hash, payload)
            entry = {'registro': json.loads(payload), 'anterior': prev_hash, 'hash': line_hash}
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + '\n')
            prev_hash = line_hash
            count += 1
    os.replace(temp, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_name(target.name + '.sha256').write_text(f'{digest}  {target.name}\n', encoding='utf-8')
    return target, count


def verify_chain(folder, stream):
    """Confere a cadeia de todos os arquivos, em ordem. Devolve (registros conferidos, lista de problemas)."""
    prev_hash, checked, problems, last_id = GENESIS, 0, [], 0
    for path in _files(folder, stream):
        with path.open(encoding='utf-8') as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                entry = json.loads(line)
                payload = json.dumps(entry['registro'], ensure_ascii=False, sort_keys=True)
                where = f'{path.name}, linha {number}'
                if entry['anterior'] != prev_hash:
                    problems.append(f'{where}: não continua a linha anterior (linha apagada ou fora de ordem)')
                if _line_hash(entry['anterior'], payload) != entry['hash']:
                    problems.append(f'{where}: conteúdo alterado')
                if entry['registro']['id'] <= last_id:
                    problems.append(f'{where}: id repetido ou fora de ordem')
                prev_hash, last_id = entry['hash'], entry['registro']['id']
                checked += 1
    return checked, problems


def status(folder):
    """Situação de cada tipo para a tela de Manutenção: arquivos, último registro exportado, último hash,
    data do arquivo mais recente e quantos registros ainda faltam exportar."""
    from django.apps import apps

    result = []
    for stream, model_path in STREAMS.items():
        files = _files(folder, stream) if folder and Path(folder).is_dir() else []
        last_id, last_hash = last_state(folder, stream) if files else (0, None)
        latest = (
            timezone.localtime(datetime.fromtimestamp(files[-1].stat().st_mtime, tz=timezone.get_current_timezone()))
            if files
            else None
        )
        pending = apps.get_model(*model_path).objects.filter(pk__gt=last_id).count()
        result.append({'stream': stream, 'files': len(files), 'last_id': last_id, 'last_hash': last_hash,
                       'latest': latest, 'pending': pending})  # fmt: skip
    return result


# ---------- Exportação completa: trava e âncora externa (G-1 da 2ª auditoria)

LOCK_ID = 7_310_402  # número fixo da trava no PostgreSQL (pg_advisory_lock) para a exportação


class ExportBusy(Exception):
    """Outra exportação está rodando agora (worker e comando manual ao mesmo tempo)."""


def export_all(folder, notify=True):
    """Exporta todos os tipos com uma trava no banco: duas exportações simultâneas gerariam arquivos repetidos.
    Se algo novo saiu e notify=True, manda por e-mail o último hash de cada tipo (âncora fora do servidor).
    Devolve {tipo: (arquivo ou None, quantidade)}."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_ID])
        if not cursor.fetchone()[0]:
            raise ExportBusy('Outra exportação da auditoria está em andamento.')
        try:
            results = {stream: export(folder, stream) for stream in STREAMS}
        finally:
            cursor.execute('SELECT pg_advisory_unlock(%s)', [LOCK_ID])
    if notify and any(count for _, count in results.values()):
        send_anchor(folder)
    return results


def send_anchor(folder):
    """E-mail com o último registro e o último hash de cada tipo. Guardado na caixa de quem recebe, fora do
    servidor, ele permite provar depois que a cadeia não foi refeita: um hash diferente do enviado denuncia a troca.
    Vai para AUDIT_EXPORT_EMAIL ou, sem ele, para o e-mail de alertas de erro (Configurações > E-mail)."""
    from django.conf import settings

    from .models import EmailSettings, SiteSettings
    from .outbox import enqueue

    to = settings.AUDIT_EXPORT_EMAIL or EmailSettings.load().error_alert_email
    if not to:
        return None
    lines = [
        'Exportação da auditoria concluída. Guarde esta mensagem: os valores abaixo servem para conferir, no futuro,',
        'que os arquivos exportados não foram refeitos.',
        '',
    ]
    for stream in STREAMS:
        last_id, last_hash = last_state(folder, stream)
        lines.append(f'{stream}: último registro {last_id}, hash {last_hash}')
    lines += ['', f'Gerado em {timezone.localtime():%d/%m/%Y %H:%M}.']
    portal = SiteSettings.load().portal_name
    return enqueue(to, f'{portal}: âncora da exportação da auditoria', '\n'.join(lines), group='audit-anchor')
