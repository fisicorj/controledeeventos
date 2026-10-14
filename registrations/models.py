import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models

from activities.models import Activity, RegistrationQuestion, SpotCategory


class Registration(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = 'CONFIRMED', 'Confirmada'
        WAITLISTED = 'WAITLISTED', 'Lista de espera'
        CANCELLED = 'CANCELLED', 'Cancelada'

    # PROTECT: apagar uma conta nunca leva junto inscrições, presenças e respostas (QUA-06 da 2ª auditoria).
    # Para tirar os dados pessoais de alguém, use a exclusão de conta (accounts/privacy.py), que anonimiza.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='registrations')
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='registrations')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)
    qr_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Momento em que entrou na fila; define a ordem de chamada.
    waitlisted_at = models.DateTimeField(null=True, blank=True)
    category = models.ForeignKey(
        SpotCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='registrations',
        verbose_name='Tipo de vaga',
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Evita mandar o lembrete de véspera mais de uma vez.
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    # Saiu da lista de espera e precisa confirmar a vaga até esta hora (a vaga fica reservada enquanto isso).
    # Vazio: inscrição confirmada de fato.
    offer_expires_at = models.DateTimeField('Confirmar a vaga até', null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'activity'], name='unique_activity_registration')]
        indexes = [models.Index(fields=['activity', 'status', 'waitlisted_at'], name='registration_queue_idx')]

    def __str__(self):
        return f'{self.user} - {self.activity}'

    @property
    def offer_pending(self):
        return self.status == self.Status.CONFIRMED and self.offer_expires_at is not None

    def waitlist_position(self):
        if self.status != self.Status.WAITLISTED or self.waitlisted_at is None:
            return None
        ahead = Registration.objects.filter(
            activity_id=self.activity_id, status=self.Status.WAITLISTED, waitlisted_at__lt=self.waitlisted_at
        ).count()
        return ahead + 1


class RegistrationAnswer(models.Model):
    registration = models.ForeignKey(Registration, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(RegistrationQuestion, on_delete=models.CASCADE, related_name='answers')
    value = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['registration', 'question'], name='unique_answer_per_question')]

    def __str__(self):
        return f'{self.question}: {self.value}'


class SurveyResponse(models.Model):
    """Resposta da pesquisa de satisfação. A organização vê as notas e comentários sem o nome de quem respondeu."""

    registration = models.OneToOneField(Registration, on_delete=models.CASCADE, related_name='survey')
    score = models.PositiveSmallIntegerField('Nota', validators=[MaxValueValidator(10)])
    comment = models.TextField('Comentário', blank=True, max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'resposta da pesquisa'
        verbose_name_plural = 'respostas da pesquisa'

    def __str__(self):
        return f'{self.score} - {self.registration.activity}'
