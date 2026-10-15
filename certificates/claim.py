"""Vínculo de certificado avulso pelo código.

Quem recebeu um certificado avulso num e-mail antigo (ou sem e-mail na planilha) informa o código em
"Minhas horas", confere os dados e confirma. Para ninguém somar horas de outra pessoa (SEC-24 da 3ª auditoria):
- só certificados de emissão avulsa, válidos, de lote não cancelado e ainda sem dono;
- o nome no certificado precisa bater com o nome da conta (primeiro e último nome, sem acento e sem diferença
  de maiúsculas);
- se a planilha trouxe matrícula ou CPF, ele também precisa bater: a matrícula tem de estar confirmada na conta
  (aparece inteira na validação pública, então digitar não basta); o CPF, que a validação mostra mascarado, tem
  de ser o mesmo informado na conta;
- certificado que já pertence a outra conta (pelo e-mail ou pela matrícula confirmada) não é vinculado: não
  conta em duas contas;
- tentativas limitadas por conta;
- o vínculo fica na auditoria do certificado, aparece marcado em "Minhas horas" e na declaração, o e-mail da
  planilha (se houver) é avisado e a própria pessoa pode desfazer.
"""

import unicodedata

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.documents import cpf_digits, is_cpf
from accounts.models import User
from core import throttle
from core.models import SiteSettings

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
    if problem := _document_problem(user, certificate.participant_document):
        return None, problem
    if _owned_by_other_account(user, certificate):
        return None, 'Este certificado já pertence a outra conta. Fale com quem emitiu.'
    return certificate, None


def _document_problem(user, document):
    """Motivo para recusar pelo documento da planilha; None se confere (ou se a planilha não trouxe documento)."""
    document = (document or '').strip()
    if not document:
        return None
    mine = (user.institutional_id or '').strip()
    if is_cpf(document):
        if cpf_digits(mine) != cpf_digits(document):
            return 'O CPF no certificado não confere com o CPF da sua conta (em "Meus dados").'
        return None
    if mine.lower() != document.lower() or not user.student_id_confirmed_in(SiteSettings.load()):
        return (
            'Este certificado tem matrícula. Para vincular, a sua conta precisa ter a mesma matrícula confirmada '
            '(e-mail institucional confirmado em "Meus dados").'
        )
    return None


def _owned_by_other_account(user, certificate):
    """Outra conta já vê este certificado pelo e-mail confirmado ou pela matrícula confirmada?"""
    condition = Q()
    if certificate.participant_email:
        condition |= Q(email__iexact=certificate.participant_email)
    if certificate.participant_document:
        condition |= Q(institutional_id__iexact=certificate.participant_document)
    if not condition:
        return False
    candidates = User.objects.filter(condition, email_verified=True, is_active=True).exclude(pk=user.pk)
    return any(owns(candidate, certificate) for candidate in candidates[:20])


@transaction.atomic
def claim(user, certificate, request=None):
    """Vincula, se ainda estiver livre (outra pessoa pode ter vinculado entre a conferência e a confirmação)."""
    updated = Certificate.objects.filter(pk=certificate.pk, claimed_by__isnull=True).update(
        claimed_by=user, claimed_at=timezone.now()
    )
    if updated:
        audit.record(audit.Action.CLAIMED, [certificate], request=request, details={'account': user.username})
        _notify_sheet_email(user, certificate)
    return bool(updated)


def _notify_sheet_email(user, certificate):
    """Avisa o e-mail da planilha: se não foi a própria pessoa, ela sabe e pode procurar quem emitiu."""
    if not certificate.participant_email:
        return
    from core.outbox import enqueue

    portal = SiteSettings.load().portal_name
    body = (
        f'Olá.\n\nO certificado "{certificate.activity_title}" (código {certificate.code}), emitido para este '
        f'e-mail no {portal}, foi vinculado à conta de {user.get_full_name() or user.username}, que o informou '
        'pelo código.\n\nSe foi você, não precisa fazer nada. Se não foi, responda a quem emitiu o certificado '
        'para que o vínculo seja revisto.'
    )
    enqueue(certificate.participant_email, f'{portal}: certificado vinculado a uma conta', body, group='claim')


@transaction.atomic
def unclaim(user, certificate, request=None):
    updated = Certificate.objects.filter(pk=certificate.pk, claimed_by=user).update(claimed_by=None, claimed_at=None)
    if updated:
        audit.record(audit.Action.UNCLAIMED, [certificate], request=request, details={'account': user.username})
    return bool(updated)
