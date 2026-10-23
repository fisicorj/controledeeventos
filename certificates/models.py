import secrets

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from activities.models import Activity, Speaker
from core.storage import private_storage as _private_storage
from registrations.models import Registration, SessionEnrollment
from submissions.models import Submission

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
    # Como em Activity.signer_name: preenchido na transferência, para a assinatura dos certificados não mudar.
    signer_name = models.CharField('Assina os certificados', max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Lote cancelado: todos os certificados foram revogados. Nada é apagado, por auditoria.
    cancelled_at = models.DateTimeField('Cancelado em', null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+'
    )
    cancel_reason = models.TextField('Motivo do cancelamento', blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F('start_date')), name='batch_end_not_before_start'),
            models.CheckConstraint(condition=Q(issue_date__gte=F('start_date')), name='batch_issue_not_before_start'),
        ]
        verbose_name = 'emissão avulsa'
        verbose_name_plural = 'emissões avulsas'

    def __str__(self):
        return self.title

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None


SOURCES = ('registration', 'batch', 'session_enrollment', 'speaker', 'submission')


def _one_source():
    """Exatamente uma origem preenchida (inscrição, lote avulso, sessão, palestrante ou trabalho)."""
    condition = None
    for chosen in SOURCES:
        part = Q(**{f'{name}__isnull': name != chosen for name in SOURCES})
        condition = part if condition is None else condition | part
    return condition


class Certificate(models.Model):
    # Cada certificado vem de uma inscrição (atividade do sistema), de uma sessão escolhida (atividade com
    # certificado por sessão) ou de uma emissão avulsa. Só um dos três.
    registration = models.OneToOneField(
        Registration, on_delete=models.PROTECT, related_name='certificate', null=True, blank=True
    )
    session_enrollment = models.OneToOneField(
        SessionEnrollment, on_delete=models.PROTECT, related_name='certificate', null=True, blank=True
    )
    batch = models.ForeignKey(
        CertificateBatch, on_delete=models.PROTECT, related_name='certificates', null=True, blank=True
    )
    # Certificados de palestrante e de apresentação de trabalho (certificates/roles.py).
    speaker = models.OneToOneField(Speaker, on_delete=models.PROTECT, related_name='certificate', null=True, blank=True)
    # Apresentação: um por autor do trabalho. coauthor_email vazio é o autor de contato (quem submeteu);
    # preenchido, é o coautor com esse e-mail.
    submission = models.ForeignKey(
        Submission, on_delete=models.PROTECT, related_name='certificates', null=True, blank=True
    )
    coauthor_email = models.EmailField(blank=True)

    class Role(models.TextChoices):
        ATTENDEE = '', 'Participação'
        SPEAKER = 'SPEAKER', 'Palestrante'
        PRESENTER = 'PRESENTER', 'Apresentação de trabalho'

    role = models.CharField('Tipo de certificado', max_length=20, choices=Role.choices, default='', blank=True)
    # Palestrante: as sessões em que falou. Apresentação: os autores do trabalho.
    role_detail = models.CharField(max_length=600, blank=True)
    code = models.CharField('Código de validação', max_length=14, unique=True, default=generate_code, editable=False)
    # Os dados abaixo são uma "foto" do momento da emissão: o certificado continua válido e igual
    # mesmo que o nome do participante ou o título da atividade mudem depois.
    participant_name = models.CharField(max_length=300)
    participant_document = models.CharField('Matrícula/RA', max_length=50, blank=True)
    activity_title = models.CharField(max_length=200)
    # Certificado por sessão: activity_title é o título da sessão e aqui fica o nome do evento.
    event_title = models.CharField(max_length=200, blank=True)
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
    # Emissão avulsa com outro e-mail: a própria pessoa vincula pelo código (certificates/claim.py)
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='claimed_certificates'
    )
    claimed_at = models.DateTimeField('Vinculado em', null=True, blank=True)

    class Meta:
        ordering = ['-issued_at']
        constraints = [
            models.CheckConstraint(condition=_one_source(), name='certificate_one_source'),
            models.UniqueConstraint(
                fields=['submission', 'coauthor_email'],
                condition=Q(submission__isnull=False),
                name='certificate_unique_author_per_submission',
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
    def owner_registration(self):
        """Inscrição de quem recebeu o certificado (de atividade ou de sessão); None na emissão avulsa."""
        if self.registration_id:
            return self.registration
        if self.session_enrollment_id:
            return self.session_enrollment.registration
        return None

    @property
    def source_activity(self):
        if self.speaker_id:
            return self.speaker.activity
        if self.submission_id:
            return self.submission.call.activity
        registration = self.owner_registration
        return registration.activity if registration else None

    @property
    def full_title(self):
        """Título para listas: "Sessão (Evento)", "Palestrante: Evento", "Trabalho (Evento)"."""
        if self.role == self.Role.SPEAKER:
            return f'Palestrante: {self.activity_title}'
        if self.role == self.Role.PRESENTER:
            return f'Apresentação: {self.activity_title} ({self.event_title})'
        return f'{self.activity_title} ({self.event_title})' if self.event_title else self.activity_title

    @property
    def document_is_cpf(self):
        from accounts.documents import is_cpf

        return is_cpf(self.participant_document)

    @property
    def public_document(self):
        """Documento como aparece na validação pública: CPF mascarado (LGPD); matrícula inteira."""
        from accounts.documents import mask_cpf

        return mask_cpf(self.participant_document)

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
        CLAIMED = 'CLAIMED', 'Vinculado a uma conta'
        UNCLAIMED = 'UNCLAIMED', 'Vínculo desfeito'

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
    'participant_name': 'Nome',
    'participant_document': 'Matrícula/CPF',
    'activity_title': 'Título',
    'activity_type': 'Tipo',
    'start_date': 'Início',
    'end_date': 'Término',
    'workload_hours': 'Carga horária',
    'document_date': 'Data do certificado',
}


class ZipExport(models.Model):
    """ZIP de um lote grande, montado em segundo plano (DES-02). Ver certificates/zips.py."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Na fila'
        RUNNING = 'RUNNING', 'Gerando'
        READY = 'READY', 'Pronto'
        FAILED = 'FAILED', 'Falhou'

    batch = models.ForeignKey(CertificateBatch, on_delete=models.CASCADE, related_name='zip_exports')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    file = models.FileField(upload_to='zips/', storage=_private_storage, blank=True)
    count = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'created_at'], name='zipexport_status_idx')]

    def __str__(self):
        return f'ZIP do lote {self.batch_id} ({self.get_status_display()})'


class HoursDeclaration(models.Model):
    """Declaração de horas emitida em "Minhas horas" (certificates/declaration.py). Guarda o que foi impresso,
    para a página pública de conferência mostrar a lista oficial: um PDF editado não bate com ela."""

    code = models.CharField('Código', max_length=14, unique=True, default=generate_code, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='hours_declarations')
    holder_name = models.CharField('Nome', max_length=300)
    semester = models.CharField('Semestre', max_length=10, blank=True)  # em branco: todos
    items = models.JSONField(default=list)  # [{codigo, atividade, tipo, data, horas, vinculado}]
    total_hours = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField('Emitida em', auto_now_add=True)
    # Cancelada pela própria pessoa em "Minhas horas": a página pública deixa de mostrar o nome e a lista.
    cancelled_at = models.DateTimeField('Cancelada em', null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.code} - {self.holder_name}'
