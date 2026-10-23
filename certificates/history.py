"""Histórico de horas certificadas do participante, por semestre (atividades complementares).

Entram os certificados válidos (não revogados):
- das atividades do sistema em que a pessoa se inscreveu (inclusive os certificados por sessão);
- das emissões avulsas em nome dela, reconhecidos pelo e-mail confirmado da conta ou pela matrícula confirmada
  (a que bate com o e-mail institucional). CPF digitado no cadastro não basta: qualquer um poderia digitar o de
  outra pessoa;
- das emissões avulsas que a própria pessoa vinculou pelo código (certificates/claim.py);
- de apresentação dos trabalhos que ela enviou e de palestrante com o e-mail confirmado da conta.
"""

from collections import OrderedDict

from django.db.models import Q

from core.models import SiteSettings

from .models import Certificate


def ownership(user, site=None):
    """Filtro dos certificados que pertencem à pessoa."""
    condition = (
        Q(registration__user=user)
        | Q(session_enrollment__registration__user=user)
        | Q(submission__author=user, coauthor_email='')
        | Q(batch__isnull=False, claimed_by=user)
    )
    if user.email_verified and user.email:
        # Emissão avulsa e certificado de palestrante: pelo e-mail informado pela organização.
        condition |= Q(batch__isnull=False, participant_email__iexact=user.email)
        condition |= Q(speaker__isnull=False, participant_email__iexact=user.email)
        condition |= Q(submission__isnull=False, coauthor_email__iexact=user.email)  # coautor do trabalho
    if user.student_id_confirmed_in(site or SiteSettings.load()):
        condition |= Q(batch__isnull=False, participant_document__iexact=user.institutional_id.strip())
    return condition


def owns(user, certificate):
    return (
        user.is_authenticated
        and Certificate.objects.filter(ownership(user), pk=certificate.pk, batch__cancelled_at__isnull=True).exists()
    )


def certificates_of(user):
    return (
        Certificate.objects.filter(ownership(user), revoked_at__isnull=True)
        .exclude(batch__cancelled_at__isnull=False)
        .select_related(
            'registration__activity',
            'session_enrollment__registration__activity',
            'speaker__activity',
            'submission__call__activity',
            'batch',
        )
        .distinct()
    )


def semester_label(day):
    return f'{day.year}/{1 if day.month <= 6 else 2}'


def by_semester(certificates):
    """[(semestre, [certificados], total de horas)], do mais recente para o mais antigo."""
    groups = OrderedDict()
    for certificate in sorted(certificates, key=lambda c: c.certificate_date, reverse=True):
        groups.setdefault(semester_label(certificate.certificate_date), []).append(certificate)
    return [(label, items, sum(c.workload_hours or 0 for c in items)) for label, items in groups.items()]
