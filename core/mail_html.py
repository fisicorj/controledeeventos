"""Versão HTML dos e-mails, montada a partir do texto (o texto puro continua indo junto, como alternativa).

Uma só fonte de conteúdo: os templates .txt de templates/emails/. Aqui o texto vira HTML simples, com a cor e o
nome da instituição, links clicáveis e o código de confirmação em destaque. Tudo é escapado: nomes e títulos
digitados por pessoas nunca viram HTML.
"""

import re

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import escape
from django.utils.safestring import mark_safe

URL = re.compile(r'(https?://[^\s<>"\']+)')


def _linkify(escaped_line):
    def link(match):
        url = match.group(1)
        trail = ''
        while url and url[-1] in '.,;:)':  # pontuação no fim da frase não faz parte do link
            trail = url[-1] + trail
            url = url[:-1]
        return f'<a href="{url}" style="color:inherit">{url}</a>{trail}'

    return URL.sub(link, escaped_line)


def text_to_html(text):
    """Parágrafos separados por linha em branco; linha recuada (o código de confirmação) vira destaque."""
    blocks = []
    for paragraph in re.split(r'\n\s*\n', text.strip()):
        lines = paragraph.split('\n')
        if len(lines) == 1 and lines[0].startswith('    ') and lines[0].strip():
            blocks.append(
                '<p style="font-size:28px;font-weight:bold;letter-spacing:6px;margin:16px 0">'
                f'{escape(lines[0].strip())}</p>'
            )
            continue
        html_lines = [_linkify(escape(line.strip())) for line in lines]
        blocks.append('<p style="margin:0 0 14px">' + '<br>'.join(html_lines) + '</p>')
    return mark_safe('\n'.join(blocks))  # noqa: S308 (cada pedaço foi escapado acima)


def render_html(subject, text):
    from .models import SiteSettings

    site = SiteSettings.load()
    logo_url = settings.SITE_URL + site.logo.url if site.logo else ''
    return render_to_string(
        'emails/base.html',
        {
            'subject': subject,
            'content': text_to_html(text),
            'portal': site,
            'logo_url': logo_url,
            'site_url': settings.SITE_URL,
        },
    )


def with_html(message):
    """Acrescenta a versão HTML a uma EmailMessage (vira EmailMultiAlternatives, com os mesmos anexos)."""
    from django.core.mail import EmailMultiAlternatives

    if not getattr(settings, 'EMAIL_HTML', True):
        return message
    html = render_html(message.subject, message.body)
    rich = EmailMultiAlternatives(
        message.subject,
        message.body,
        from_email=message.from_email,
        to=message.to,
        reply_to=message.reply_to,
        headers=message.extra_headers,
        attachments=message.attachments,
    )
    rich.attach_alternative(html, 'text/html')
    return rich
