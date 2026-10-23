"""Formatos de imagem que o sistema abre (S-01 e S-07 da 8ª auditoria).

O Pillow reconhece dezenas de formatos (TIFF, SPIDER, EPS, MSP, XBM, BMP...), e é nos formatos raros que aparece a
maioria das falhas de segurança dele. As imagens do portal (capa, foto de palestrante, logo, fundo e assinatura do
certificado) só precisam de JPEG, PNG e WebP: os demais leitores são desligados quando o Django sobe, então um
arquivo em outro formato é recusado no envio ("não é uma imagem válida") e nunca chega a ser interpretado.
"""

ALLOWED_FORMATS = ('JPEG', 'PNG', 'WEBP')
ACCEPT = 'image/jpeg,image/png,image/webp'  # para o seletor de arquivos do navegador


def restrict_pillow():
    from PIL import Image

    Image.init()  # carrega todos os leitores uma vez; depois disso o Pillow não carrega de novo
    for name in [name for name in Image.OPEN if name not in ALLOWED_FORMATS]:
        del Image.OPEN[name]
    Image.ID[:] = [name for name in Image.ID if name in ALLOWED_FORMATS]


INVALID_IMAGE = 'Envie a imagem em JPG, PNG ou WebP. Outros formatos (como GIF, BMP ou TIFF) não são aceitos.'


def setup_image_fields(form):
    """Campos de imagem do formulário: o seletor do navegador mostra só os formatos aceitos, e a mensagem de
    erro diz quais são."""
    from django import forms

    for field in form.fields.values():
        if isinstance(field, forms.ImageField):
            field.widget.attrs['accept'] = ACCEPT
            field.error_messages['invalid_image'] = INVALID_IMAGE
            hint = 'JPG, PNG ou WebP.'
            field.help_text = f'{field.help_text} {hint}'.strip() if field.help_text else hint
