"""Armazenamento privado: arquivos que não podem ficar públicos em /media/.

Usado pelos materiais pós-evento e pela assinatura e arte do certificado. Esses arquivos só saem por views que
conferem quem pode acessar (ou são lidos direto pelo gerador de PDF). A pasta é settings.PRIVATE_MEDIA_ROOT,
lida a cada uso (assim os testes podem trocá-la)."""

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None

    def url(self, name):
        # Não há endereço público. O "#" mantém o "Atualmente / Limpar" dos formulários funcionando;
        # a visualização real passa por views que conferem o acesso.
        return '#'


def private_storage():
    return PrivateStorage()
