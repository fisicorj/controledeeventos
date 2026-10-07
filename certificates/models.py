import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from activities.models import Activity
from registrations.models import Registration

# Sem letras e números que se confundem (0/O, 1/I/L), para facilitar a digitação na validação.
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


def generate_code():
    raw = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    return f'{raw[:4]}-{raw[4:8]}-{raw[8:]}'


class CertificateBatch(models.Model):
    """Emissão avulsa: certificados de um evento que não foi cadastrado no sistema,
    com a lista de participantes importada de uma planilha."""

    title = models.CharField('Título do evento', max_length=200)
    activity_type = models.CharField('Tipo', max_length=20, choices=Activity.Type.choices, default=Activity.Type.EVENT)
    start_date = models.DateField('Data de início')
    end_date = models.DateField('Data de término')
    workload_hours = models.PositiveSmallIntegerField('Carga horária (horas)', null=True, blank=True)
    issue_date = models.DateField(
        'Data do certificado',
        default=timezone.localdate,
        help_text='A data que aparece no certificado. Pode ser retroativa, por exemplo o último dia do evento.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='certificate_batches',
        verbose_name='Emitido por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Lote cancelado: todos os certificados foram revogados. Nada é apagado, por auditoria.
    cancelled_at = models.DateTimeField('Cancelado em', null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+'
    )
    cancel_reason = models.TextField('Motivo do cancelamento', blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'emissão avulsa'
        verbose_name_plural = 'emissões avulsas'

    def __str__(self):
        return self.title

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None


class Certificate(models.Model):
    # Cada certificado vem de uma inscrição (atividade do sistema) ou de uma emissão avulsa.
    registration = models.OneToOneField(
        Registration, on_delete=models.PROTECT, related_name='certificate', null=True, blank=True
    )
    batch = models.ForeignKey(
        CertificateBatch, on_delete=models.PROTECT, related_name='certificates', null=True, blank=True
    )
    code = models.CharField('Código de validação', max_length=14, unique=True, default=generate_code, editable=False)
    # Os dados abaixo são uma "foto" do momento da emissão: o certificado continua válido e igual
    # mesmo que o nome do participante ou o título da atividade mudem depois.
    participant_name = models.CharField(max_length=300)
    participant_document = models.CharField('Matrícula/RA', max_length=50, blank=True)
    activity_title = models.CharField(max_length=200)
    activity_type = models.CharField(max_length=40)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    workload_hours = models.PositiveSmallIntegerField(null=True, blank=True)
    attendance_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    # Data impressa no certificado quando difere do dia em que foi gerado (emissão avulsa retroativa).
    # issued_at continua registrando quando o certificado foi de fato gerado no sistema.
    document_date = models.DateField('Data do certificado', null=True, blank=True)
    # Só na emissão avulsa (vêm da planilha)
    participant_group = models.CharField('Turma', max_length=100, blank=True)
    participant_email = models.EmailField('E-mail', blank=True)
    emailed_at = models.DateTimeField('Enviado por e-mail em', null=True, blank=True)
    # Revogação: o certificado nunca é apagado; a validação pública mostra que foi revogado.
    revoked_at = models.DateTimeField('Revogado em', null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+'
    )
    revoked_reason = models.TextField('Motivo da revogação', blank=True)

    class Meta:
        ordering = ['-issued_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(registration__isnull=False, batch__isnull=True)
                | Q(registration__isnull=True, batch__isnull=False),
                name='certificate_registration_xor_batch',
            ),
            models.UniqueConstraint(
                fields=['batch', 'participant_document'],
                condition=Q(batch__isnull=False, revoked_at__isnull=True) & ~Q(participant_document=''),
                name='certificate_unique_document_per_batch',
            ),
        ]

    def __str__(self):
        return f'{self.code} - {self.participant_name}'

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    @property
    def source_label(self):
        return 'Emissão avulsa' if self.batch_id else 'Atividade'

    @property
    def certificate_date(self):
        """Data que aparece no certificado e na validação."""
        if self.document_date:
            return self.document_date
        return timezone.localtime(self.issued_at).date() if self.issued_at else timezone.localdate()


class CertificateEvent(models.Model):
    """Registro de auditoria das emissões. Só recebe inclusões: não pode ser alterado nem apagado
    (o banco também recusa UPDATE e DELETE nesta tabela, ver a migração 0005).

    Cada linha guarda uma cópia dos dados principais do certificado, para continuar legível sozinha."""

    class Action(models.TextChoices):
        ISSUED = 'ISSUED', 'Emitido'
        UPDATED = 'UPDATED', 'Alterado'
        REVOKED = 'REVOKED', 'Revogado'
        EMAILED = 'EMAILED', 'Enviado por e-mail'
        DOWNLOADED = 'DOWNLOADED', 'PDF baixado'

    created_at = models.DateTimeField('Data e hora', default=timezone.now, editable=False, db_index=True)
    action = models.CharField('Ação', max_length=20, choices=Action.choices, db_index=True)
    certificate = models.ForeignKey(Certificate, on_delete=models.PROTECT, related_name='events')
    batch = models.ForeignKey(CertificateBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='events')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='certificate_events'
    )
    actor_name = models.CharField('Usuário', max_length=300, blank=True)
    ip = models.GenericIPAddressField('IP', null=True, blank=True)
    # Cópia dos dados do certificado no momento do evento
    code = models.CharField('Código', max_length=14, db_index=True)
    participant_name = models.CharField('Participante', max_length=300)
    participant_document = models.CharField('Matrícula/RA', max_length=50, blank=True)
    activity_title = models.CharField('Atividade/evento', max_length=200)
    details = models.JSONField('Detalhes', default=dict, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'evento de certificado'
        verbose_name_plural = 'auditoria de certificados'

    def __str__(self):
        return f'{self.get_action_display()} {self.code}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError('O registro de auditoria não pode ser alterado.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError('O registro de auditoria não pode ser apagado.')

    @property
    def summary(self):
        """Texto curto com os detalhes, para a tela e o CSV."""
        d = self.details or {}
        parts = []
        if d.get('reason'):
            parts.append(f'Motivo: {d["reason"]}')
        if d.get('recipient'):
            parts.append(f'Para: {d["recipient"]}')
        if d.get('via'):
            parts.append({'zip': 'no ZIP do lote', 'pdf': 'PDF individual'}.get(d['via'], d['via']))
        for field, change in (d.get('changes') or {}).items():
            parts.append(f'{FIELD_LABELS.get(field, field)}: {change[0] or "(vazio)"} → {change[1] or "(vazio)"}')
        if d.get('source'):
            parts.append(d['source'])
        if d.get('backfill'):
            parts.append('Registrado na criação da auditoria (emissão anterior)')
        return ' · '.join(parts)


FIELD_LABELS = {
    'activity_title': 'Título',
    'activity_type': 'Tipo',
    'start_date': 'Início',
    'end_date': 'Término',
    'workload_hours': 'Carga horária',
    'document_date': 'Data do certificado',
}
