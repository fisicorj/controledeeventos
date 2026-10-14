"""Vínculo de certificado avulso pelo código.

Quem recebeu um certificado avulso num e-mail antigo (ou sem e-mail na planilha) informa o código em
"Minhas horas", confere os dados e confirma. Para ninguém somar horas de outra pessoa:
- só certificados de emissão avulsa, válidos, de lote não cancelado e ainda sem dono;
- o nome no certificado precisa bater com o nome da conta (primeiro e último nome, sem acento e sem diferença
  de maiúsculas);
- tentativas limitadas por conta;
- o vínculo fica na auditoria do certificado e pode ser desfeito pela própria pessoa.
"""

import unicodedata

from django.db import transaction
from django.utils import timezone

from core import throttle

from . import audit
from .history import owns
from .models import Certificate

ATTEMPTS, PERIOD = 10, 3600  # códigos digitados por conta, por hora


def _tokens(name):
    plain = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
    return [part for part in ''.join(ch if ch.isalnum() else ' ' for ch in plain.lower()).split() if part]


def names_match(user, participant_name):
    """Primeiro e último nome da conta iguais aos do certificado ('Ana Lima' bate com 'ANA DE SOUZA LIMA')."""
    mine, theirs = _tokens(user.get_full_name()), _tokens(participant_name)
    if len(mine) < 2 or len(theirs) < 2:
        return mine == theirs and bool(mine)
    return mine[0] == theirs[0] and mine[-1] == theirs[-1]


def blocked(user):
    return throttle.blocked(ATTEMPTS, PERIOD, 'claim', user.pk)


def find(user, code):
    """(certificado, None) se pode ser vinculado; (None ou certificado, motivo) se não."""
    throttle.hit(ATTEMPTS, PERIOD, 'claim', user.pk)
    certificate = Certificate.objects.select_related('batch').filter(code=code or '').first()
    if certificate is None:
        return None, 'Não encontramos certificado com esse código. Confira as letras e os números.'
    if owns(user, certificate):
        return certificate, 'Este certificado já aparece nas suas horas.'
    if certificate.batch_id is None:
        return None, 'Só certificados de emissão avulsa podem ser vinculados. Os das atividades já entram sozinhos.'
    if certificate.is_revoked or certificate.batch.is_cancelled:
        return None, 'Este certificado foi revogado e não pode ser vinculado.'
    if certificate.claimed_by_id:
        return None, 'Este certificado já foi vinculado a outra conta. Fale com quem emitiu.'
    if not names_match(user, certificate.participant_name):
        return None, (
            'O nome no certificado não confere com o nome da sua conta. Corrija o nome em "Meus dados" ou fale '
            'com quem emitiu.'
        )
    return certificate, None


@transaction.atomic
def claim(user, certificate, request=None):
    """Vincula, se ainda estiver livre (outra pessoa pode ter vinculado entre a conferência e a confirmação)."""
    updated = Certificate.objects.filter(pk=certificate.pk, claimed_by__isnull=True).update(
        claimed_by=user, claimed_at=timezone.now()
    )
    if updated:
        audit.record(audit.Action.CLAIMED, [certificate], request=request, details={'account': user.username})
    return bool(updated)


@transaction.atomic
def unclaim(user, certificate, request=None):
    updated = Certificate.objects.filter(pk=certificate.pk, claimed_by=user).update(claimed_by=None, claimed_at=None)
    if updated:
        audit.record(audit.Action.UNCLAIMED, [certificate], request=request, details={'account': user.username})
    return bool(updated)
