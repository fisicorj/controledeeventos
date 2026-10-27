"""Criptografia de segredos guardados no banco (senha do SMTP e segredos da verificação em duas etapas).

Chave: FIELD_ENCRYPTION_KEY, própria para isso. Assim, quem descobrir só a SECRET_KEY não lê esses segredos, e a
SECRET_KEY pode ser trocada sem perder nada. Sem FIELD_ENCRYPTION_KEY, a chave é derivada da SECRET_KEY (como nas
versões anteriores).

Para ler, vale qualquer chave da lista: a atual, as antigas em FIELD_ENCRYPTION_KEY_FALLBACKS, a SECRET_KEY e as
antigas em SECRET_KEY_FALLBACKS. Para gravar, só a atual. A faxina diária (reencrypt_all) regrava com a chave atual
o que ainda estiver com uma antiga; depois disso as antigas podem sair do .env.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


class SecretUnreadable(Exception):
    pass


def _derive(secret):
    digest = hashlib.sha256(f'arena-events:secrets:{secret}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _secrets():
    """Chaves em ordem: a primeira grava; todas leem."""
    field_key = getattr(settings, 'FIELD_ENCRYPTION_KEY', '')
    candidates = [field_key] if field_key else []
    candidates += list(getattr(settings, 'FIELD_ENCRYPTION_KEY_FALLBACKS', []))
    candidates += [settings.SECRET_KEY, *getattr(settings, 'SECRET_KEY_FALLBACKS', [])]
    unique = []
    for secret in candidates:
        if secret and secret not in unique:
            unique.append(secret)
    return unique


def _fernet():
    return MultiFernet([_derive(secret) for secret in _secrets()])


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode() if text else ''


def decrypt(token):
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretUnreadable(
            'Um segredo guardado não pôde ser lido: a chave mudou (SECRET_KEY ou FIELD_ENCRYPTION_KEY) e a antiga não '
            'está em SECRET_KEY_FALLBACKS / FIELD_ENCRYPTION_KEY_FALLBACKS. Cadastre-o novamente.'
        ) from exc


def needs_reencrypt(token):
    """O token foi gravado com uma chave que não é a atual?"""
    if not token:
        return False
    try:
        _derive(_secrets()[0]).decrypt(token.encode())
    except InvalidToken:
        return True
    return False


def reencrypt(token):
    """O mesmo segredo, gravado com a chave atual. Levanta SecretUnreadable se nenhuma chave o lê."""
    try:
        return _fernet().rotate(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretUnreadable('Segredo ilegível com as chaves configuradas.') from exc


def reencrypt_all():
    """Regrava com a chave atual a senha do SMTP e os segredos da verificação em duas etapas que ainda estiverem com
    uma chave antiga. Devolve quantos foram regravados. Os ilegíveis ficam como estão (e continuam pedindo cadastro
    novo, como antes)."""
    from accounts.models import MfaDevice

    from .models import EmailSettings

    changed = 0
    settings_row = EmailSettings.load(fresh=True)
    if needs_reencrypt(settings_row.password_encrypted):
        try:
            settings_row.password_encrypted = reencrypt(settings_row.password_encrypted)
            settings_row.save(update_fields=['password_encrypted'])
            changed += 1
        except SecretUnreadable:
            pass
    for device in MfaDevice.objects.exclude(secret='').only('pk', 'secret').iterator():
        if not needs_reencrypt(device.secret):
            continue
        try:
            device.secret = reencrypt(device.secret)
        except SecretUnreadable:
            continue
        device.save(update_fields=['secret'])
        changed += 1
    return changed
