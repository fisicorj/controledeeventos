"""Verificação em duas etapas para administradores e organizadores (SEC-02 da auditoria).

TOTP (RFC 6238, o padrão dos aplicativos Google Authenticator, Microsoft Authenticator, Authy, 2FAS etc.):
código de 6 dígitos que muda a cada 30 segundos, calculado a partir de um segredo compartilhado.
Implementado com a biblioteca padrão (hmac), sem dependência nova.
"""

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import crypto

from .models import MfaDevice, User

PERIOD, DIGITS, WINDOW = 30, 6, 1  # aceita o código do intervalo anterior e do seguinte (relógio adiantado)
RECOVERY_COUNT = 10
SESSION_KEY = 'mfa_verified_user'


# ---------- Quem precisa


def applies_to(user):
    """Contas com poder sobre dados de terceiros: administradores, organizadores, acesso ao painel /admin/ e
    quem coordena alguma chamada de trabalhos (vê os autores, baixa os arquivos e decide os trabalhos)."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff or user.role in (User.Role.ADMIN, User.Role.ORGANIZER):
        return True
    return coordinates_call(user)


def coordinates_call(user):
    """Guardado no objeto: o middleware pergunta a cada requisição."""
    if not hasattr(user, '_coordinates_call'):
        user._coordinates_call = user.coordinated_calls.exists()
    return user._coordinates_call


def device_for(user):
    return MfaDevice.objects.filter(user=user, confirmed_at__isnull=False).first()


def is_verified(request):
    return request.session.get(SESSION_KEY) == request.user.pk


def mark_verified(request):
    from . import reauth

    request.session[SESSION_KEY] = request.user.pk
    reauth.mark(request)  # a segunda etapa conta como identidade confirmada agora
    request.session.cycle_key()  # sessão nova depois de subir de nível


# ---------- TOTP


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def totp(secret, step):
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_step(now=None):
    return int((time.time() if now is None else now) // PERIOD)


def matching_step(secret, code, last_step=0, now=None):
    """Intervalo em que o código vale (dentro da janela e depois do último usado), ou None."""
    code = ''.join(code.split())
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if candidate > last_step and hmac.compare_digest(totp(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret, user, issuer):
    label = quote(f'{issuer}:{user.email or user.username}')
    query = urlencode({'secret': secret, 'issuer': issuer, 'digits': DIGITS, 'period': PERIOD})
    return f'otpauth://totp/{label}?{query}'


def qr_svg(text):
    """QR Code em SVG (vai direto na página; o segredo não sai do servidor para gerar a imagem)."""
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode()


# ---------- Códigos de recuperação


def _hash_code(code):
    normalized = ''.join(code.split()).replace('-', '').lower()
    secret = f'arena:mfa-recovery:{settings.SECRET_KEY}'.encode()
    return hmac.new(secret, normalized.encode(), hashlib.sha256).hexdigest()


def new_recovery_codes(device):
    """Gera códigos novos (os anteriores deixam de valer) e devolve os códigos em texto, para mostrar uma vez."""
    alphabet = 'abcdefghjkmnpqrstuvwxyz23456789'
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = ''.join(secrets.choice(alphabet) for _ in range(10))
        codes.append(f'{raw[:5]}-{raw[5:]}')
    device.recovery_codes = [_hash_code(c) for c in codes]
    device.save(update_fields=['recovery_codes'])
    return codes


# ---------- Cadastro e verificação


def pending_device(user):
    """Cadastro em andamento (ou um novo), com o segredo já gerado."""
    device, _ = MfaDevice.objects.get_or_create(user=user, defaults={'secret': crypto.encrypt(new_secret())})
    return device


def secret_of(device):
    return crypto.decrypt(device.secret)


def confirm(device, code):
    """Primeiro código certo ativa o cadastro. Devolve os códigos de recuperação, ou None se o código não vale."""
    step = matching_step(secret_of(device), code)
    if step is None:
        return None
    device.confirmed_at, device.last_step = timezone.now(), step
    device.save(update_fields=['confirmed_at', 'last_step'])
    return new_recovery_codes(device)


def verify_totp(user, code):
    """Só o código do aplicativo, sem tocar nos códigos de recuperação. Para as telas que aceitam apenas o
    aplicativo (confirmar identidade, gerar códigos novos, desativar): antes, um código de recuperação digitado
    ali era gasto e a tela recusava mesmo assim (SEC-23 da 3ª auditoria)."""
    with transaction.atomic():
        device = MfaDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
        if device is None:
            return False
        step = matching_step(secret_of(device), code, device.last_step)
        if step is None:
            return False
        device.last_step = step
        device.save(update_fields=['last_step'])
        return True


def verify(user, code):
    """Confere um código do aplicativo ou de recuperação (este só vale uma vez). Devolve 'totp', 'recovery' ou None."""
    with transaction.atomic():
        device = MfaDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
        if device is None:
            return None
        step = matching_step(secret_of(device), code, device.last_step)
        if step is not None:
            device.last_step = step
            device.save(update_fields=['last_step'])
            return 'totp'
        hashed = _hash_code(code)
        for stored in device.recovery_codes:
            if hmac.compare_digest(stored, hashed):
                device.recovery_codes = [c for c in device.recovery_codes if c != stored]
                device.save(update_fields=['recovery_codes'])
                return 'recovery'
    return None


def reset(user, end_sessions=False):
    """Apaga o cadastro (troca de celular, perda do aparelho). No próximo acesso a pessoa cadastra de novo.
    end_sessions: encerra também as sessões abertas (celular perdido: quem estiver com ele não continua logado)."""
    removed = MfaDevice.objects.filter(user=user).delete()[0]
    if end_sessions:
        from .sessions import end_sessions as end

        end(user)
    return removed
