import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models

from activities.models import Activity, RegistrationQuestion, Session, SpotCategory


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


class SessionEnrollment(models.Model):
    """Sessão escolhida por quem está inscrito numa atividade com inscrição por sessão (Activity.per_session).

    Cada sessão tem as suas vagas e a sua lista de espera, chamada por ordem de chegada (registrations/sessions.py).
    Nada é apagado: quem sai fica como cancelado, para o histórico."""

    class Status(models.TextChoices):
        CONFIRMED = 'CONFIRMED', 'Confirmada'
        WAITLISTED = 'WAITLISTED', 'Lista de espera'
        CANCELLED = 'CANCELLED', 'Cancelada'

    registration = models.ForeignKey(Registration, on_delete=models.CASCADE, related_name='session_enrollments')
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='enrollments')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)
    created_at = models.DateTimeField(auto_now_add=True)
    waitlisted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)  # lembrete de véspera da sessão, uma vez só
    # Saiu da fila da sessão e precisa confirmar a vaga até esta hora (a vaga fica reservada enquanto isso), como na
    # fila do evento (Activity.waitlist_offer_hours). Vazio: vaga confirmada de fato.
    offer_expires_at = models.DateTimeField('Confirmar a vaga até', null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['registration', 'session'], name='unique_session_enrollment'),
        ]
        indexes = [models.Index(fields=['session', 'status', 'waitlisted_at'], name='session_enrollment_queue_idx')]
        verbose_name = 'inscrição na sessão'
        verbose_name_plural = 'inscrições nas sessões'

    def __str__(self):
        return f'{self.registration.user} - {self.session}'

    @property
    def is_active(self):
        return self.status != self.Status.CANCELLED

    @property
    def offer_pending(self):
        return self.status == self.Status.CONFIRMED and self.offer_expires_at is not None

    def waitlist_position(self):
        if self.status != self.Status.WAITLISTED or self.waitlisted_at is None:
            return None
        ahead = SessionEnrollment.objects.filter(
            session_id=self.session_id, status=self.Status.WAITLISTED, waitlisted_at__lt=self.waitlisted_at
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


class SessionRating(models.Model):
    """Nota de uma sessão dentro da pesquisa (M-08, modo por sessão): opcional, para cada sessão com presença."""

    response = models.ForeignKey(SurveyResponse, on_delete=models.CASCADE, related_name='session_ratings')
    session = models.ForeignKey('activities.Session', on_delete=models.CASCADE, related_name='ratings')
    score = models.PositiveSmallIntegerField('Nota', validators=[MaxValueValidator(10)])
    comment = models.TextField('Comentário', blank=True, max_length=1000)

    class Meta:
        verbose_name = 'nota da sessão'
        verbose_name_plural = 'notas das sessões'
        constraints = [models.UniqueConstraint(fields=['response', 'session'], name='unique_rating_per_session')]

    def __str__(self):
        return f'{self.score} - {self.session}'


class RegistrationLog(models.Model):
    """O que a organização fez numa inscrição (incluir, cancelar, colocar ou tirar de sessão), com quem e por quê.
    As ações do próprio participante não entram aqui: elas aparecem na situação da inscrição e das sessões."""

    class Action(models.TextChoices):
        ADDED = 'ADDED', 'Inscrito pela organização'
        CANCELLED = 'CANCELLED', 'Inscrição cancelada pela organização'
        SESSION_ADDED = 'SESSION_ADDED', 'Colocado numa sessão'
        SESSION_REMOVED = 'SESSION_REMOVED', 'Tirado de uma sessão'

    registration = models.ForeignKey(Registration, on_delete=models.CASCADE, related_name='logs')
    action = models.CharField(max_length=20, choices=Action.choices)
    session_title = models.CharField(max_length=160, blank=True)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'ação da organização na inscrição'
        verbose_name_plural = 'ações da organização nas inscrições'

    def __str__(self):
        return f'{self.get_action_display()}: {self.registration}'
