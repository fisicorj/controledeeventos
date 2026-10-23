"""Adicionar à agenda (Google, Outlook e arquivo .ics) e link do mapa."""

from datetime import UTC
from urllib.parse import quote, urlencode

from django.utils import timezone

ONLINE_WORDS = ('online', 'on-line', 'remoto', 'virtual', 'zoom', 'meet', 'teams', 'youtube')


def _utc(dt, fmt='%Y%m%dT%H%M%SZ'):
    return dt.astimezone(UTC).strftime(fmt)


def links(activity, page_url):
    details = f'{activity.description[:600]}\n\n{page_url}'
    google = 'https://calendar.google.com/calendar/render?' + urlencode(
        {
            'action': 'TEMPLATE',
            'text': activity.title,
            'dates': f'{_utc(activity.start_date)}/{_utc(activity.end_date)}',
            'details': details,
            'location': activity.location,
        }
    )
    outlook = 'https://outlook.office.com/calendar/0/deeplink/compose?' + urlencode(
        {
            'path': '/calendar/action/compose',
            'rru': 'addevent',
            'subject': activity.title,
            'startdt': activity.start_date.astimezone(UTC).isoformat(),
            'enddt': activity.end_date.astimezone(UTC).isoformat(),
            'body': details,
            'location': activity.location,
        }
    )
    return {'google': google, 'outlook': outlook}


def map_url(location):
    location = (location or '').strip()
    if not location or any(word in location.lower() for word in ONLINE_WORDS):
        return ''
    return 'https://www.google.com/maps/search/?api=1&query=' + quote(location)


def _escape(text):
    return (
        str(text)
        .replace('\\', '\\\\')
        .replace(';', '\\;')
        .replace(',', '\\,')
        .replace('\r\n', '\\n')
        .replace('\n', '\\n')
    )


def _fold(line):
    """Linhas do .ics têm no máximo 75 bytes; o resto continua na linha seguinte começando com espaço."""
    data = line.encode()
    if len(data) <= 75:
        return line
    parts, chunk = [], b''
    for char in line:
        encoded = char.encode()
        if len(chunk) + len(encoded) > (75 if not parts else 74):
            parts.append(chunk.decode())
            chunk = b''
        chunk += encoded
    parts.append(chunk.decode())
    return '\r\n '.join(parts)


def ics(activity, page_url, institution, sessions=None):
    """Arquivo .ics com um evento por sessão (ou a atividade inteira, se não houver sessões).

    sessions: só estas sessões (agenda pessoal, com as sessões escolhidas pela pessoa). Lista vazia ou None
    usam a regra acima."""
    sessions = list(sessions) if sessions else list(activity.sessions.all())
    items = (
        [
            (f'{activity.title}: {s.title}', s.start_at, s.end_at, s.location or activity.location, s.pk)
            for s in sessions
        ]
        if sessions
        else [(activity.title, activity.start_date, activity.end_date, activity.location, 0)]
    )
    stamp = _utc(timezone.now())
    host = page_url.split('/')[2] if '://' in page_url else 'arena-plus'
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', f'PRODID:-//{_escape(institution)}//Arena+//PT-BR', 'CALSCALE:GREGORIAN',
             'METHOD:PUBLISH']  # fmt: skip
    for title, start, end, location, key in items:
        lines += [
            'BEGIN:VEVENT',
            f'UID:activity-{activity.pk}-{key}@{host}',
            f'DTSTAMP:{stamp}',
            f'DTSTART:{_utc(start)}',
            f'DTEND:{_utc(end)}',
            f'SUMMARY:{_escape(title)}',
            f'DESCRIPTION:{_escape(activity.description[:1000] + chr(10) + chr(10) + page_url)}',
            f'LOCATION:{_escape(location or "")}',
            f'URL:{page_url}',
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(line) for line in lines) + '\r\n'
