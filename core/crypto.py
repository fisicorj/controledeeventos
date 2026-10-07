"""Criptografia de segredos guardados no banco (ex.: senha do SMTP).

A chave é derivada da SECRET_KEY. Se a SECRET_KEY mudar, os segredos antigos ficam ilegíveis e
precisam ser cadastrados de novo pela tela de configurações.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SecretUnreadable(Exception):
    pass


def _fernet():
    digest = hashlib.sha256(f'arena-events:secrets:{settings.SECRET_KEY}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode() if text else ''


def decrypt(token):
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretUnreadable(
            'A senha do SMTP não pôde ser lida (a SECRET_KEY mudou?). Cadastre a senha novamente.'
        ) from exc
