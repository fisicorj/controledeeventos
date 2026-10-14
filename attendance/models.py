from django.conf import settings
from django.db import models
from django.utils import timezone

from activities.models import Session
from registrations.models import Registration


class Attendance(models.Model):
    class Method(models.TextChoices):
        QR_CODE = 'QR_CODE', 'QR Code'
        MANUAL = 'MANUAL', 'Manual'

    registration = models.ForeignKey(Registration, on_delete=models.CASCADE, related_name='attendances')
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='attendances')
    # Hora da leitura. Numa leitura feita no celular sem internet, é a hora em que o QR foi lido, não a do envio.
    check_in = models.DateTimeField(default=timezone.now)
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='validated_attendances'
    )
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.QR_CODE)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['registration', 'session'], name='unique_registration_session_attendance')
        ]

    def __str__(self):
        return f'{self.registration} - {self.session or self.check_in}'


class AttendanceRemoval(models.Model):
    """Registro de presenças removidas (só de inclusão: o banco recusa UPDATE e DELETE).

    A presença decide quem recebe certificado, então quem tirou a presença de quem, quando e de onde fica guardado.
    Guarda cópias de nomes e títulos em vez de chaves estrangeiras: o registro continua legível (e não impede
    nada) mesmo depois que a sessão, a atividade ou a inscrição forem apagadas.
    """

    created_at = models.DateTimeField('Removida em', default=timezone.now, db_index=True, editable=False)
    activity_id = models.BigIntegerField('Atividade (id)', db_index=True)
    activity_title = models.CharField('Atividade', max_length=200)
    session_id = models.BigIntegerField('Sessão (id)', db_index=True)
    session_label = models.CharField('Sessão', max_length=300)
    registration_id = models.BigIntegerField('Inscrição (id)')
    participant_name = models.CharField('Participante', max_length=300)
    check_in = models.DateTimeField('Presença registrada em')
    method = models.CharField('Forma do registro', max_length=20, choices=Attendance.Method.choices)
    validated_by_name = models.CharField('Registrada por', max_length=300, blank=True)
    actor_id = models.BigIntegerField('Usuário (id)', null=True, blank=True)
    actor_name = models.CharField('Removida por', max_length=300, blank=True)
    ip = models.GenericIPAddressField('IP', null=True, blank=True)
    origin = models.CharField('Origem', max_length=40, default='Lista de presença')

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'presença removida'
        verbose_name_plural = 'presenças removidas'

    def __str__(self):
        return f'{self.participant_name} - {self.session_label} ({self.created_at:%d/%m/%Y %H:%M})'
