import io

import qrcode

QR_PREFIX = 'ARENA-EVENTS:'


def qr_png(registration):
    """PNG do QR Code da inscrição (contém só o token, nenhum dado pessoal)."""
    image = qrcode.make(f'{QR_PREFIX}{registration.qr_token}')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
