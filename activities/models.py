from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def _fmt(dt):
    return timezone.localtime(dt).strftime('%d/%m/%Y %H:%M')


class Activity(models.Model):
    class Type(models.TextChoices):
        COURSE = 'COURSE', 'Curso'
        EVENT = 'EVENT', 'Evento'
        APPOINTMENT = 'APPOINTMENT', 'Agenda'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Rascunho'
        PUBLISHED = 'PUBLISHED', 'Publicado'
        CANCELLED = 'CANCELLED', 'Cancelado'
        FINISHED = 'FINISHED', 'Finalizado'

    title = models.CharField(max_length=200)
    description = models.TextField()
    activity_type = models.CharField(max_length=20, choices=Type.choices)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    registration_start = models.DateTimeField()
    registration_end = models.DateTimeField()
    capacity = models.PositiveIntegerField()
    location = models.CharField(max_length=200, blank=True)
    image = models.ImageField(upload_to='activities/', blank=True, null=True)
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='organized_activities'
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    waitlist_enabled = models.BooleanField(
        'Lista de espera', default=True, help_text='Quando lotar, novos interessados entram numa fila.'
    )
    certificate_enabled = models.BooleanField(
        'Emitir certificados',
        default=True,
        help_text='Emitidos ao finalizar a atividade, para quem atingir a frequência.',
    )
    workload_hours = models.PositiveSmallIntegerField(
        'Carga horária (horas)', null=True, blank=True, help_text='Aparece no certificado. Opcional.'
    )
    min_attendance_percent = models.PositiveSmallIntegerField(
        'Frequência mínima para certificado (%)',
        default=75,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    @property
    def state_pill(self):
        """Selo de situação mostrado nos cards e na página (usa confirmed_count quando anotado)."""
        confirmed = getattr(self, 'confirmed_count', None)
        if confirmed is None:
            confirmed = self.registrations.filter(status='CONFIRMED').count()
        now = timezone.now()
        if self.status == self.Status.CANCELLED:
            return {'label': 'Cancelada', 'css': 'pill-danger'}
        if self.status == self.Status.FINISHED or self.end_date < now:
            return {'label': 'Encerrada', 'css': 'pill-closed'}
        if now < self.registration_start:
            return {'label': 'Inscrições em breve', 'css': 'pill-soon'}
        if now > self.registration_end:
            return {'label': 'Inscrições encerradas', 'css': 'pill-closed'}
        if confirmed >= self.capacity:
            if self.waitlist_enabled:
                return {'label': 'Lista de espera', 'css': 'pill-wait'}
            return {'label': 'Esgotado', 'css': 'pill-closed'}
        if self.capacity and confirmed / self.capacity >= 0.8:
            return {'label': 'Últimas vagas', 'css': 'pill-few'}
        return {'label': 'Vagas abertas', 'css': 'pill-open'}

    def clean(self):
        errors = {}
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            errors['end_date'] = 'O término deve ser posterior ao início.'
        if self.registration_start and self.registration_end:
            if self.registration_end <= self.registration_start:
                errors['registration_end'] = 'O fim das inscrições deve ser posterior ao início das inscrições.'
            elif self.end_date and self.registration_end > self.end_date:
                errors['registration_end'] = 'As inscrições devem encerrar até o término da atividade.'
        if self.pk and self.capacity is not None:
            from registrations.models import Registration  # import local evita ciclo entre os apps

            confirmed = self.registrations.filter(status=Registration.Status.CONFIRMED).count()
            if self.capacity < confirmed:
                errors['capacity'] = (
                    f'A capacidade não pode ser menor que o número de inscritos confirmados ({confirmed}).'
                )
        if errors:
            raise ValidationError(errors)


class Session(models.Model):
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='sessions')
    title = models.CharField(max_length=160)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True)
    check_in_open = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_at']

    def __str__(self):
        return f'{self.activity.title} - {self.title}'

    def clean(self):
        errors = {}
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            errors['end_at'] = 'O término da sessão deve ser posterior ao início.'
        try:
            activity = self.activity
        except Activity.DoesNotExist:
            activity = None
        if activity and activity.start_date and activity.end_date:
            if self.start_at and self.start_at < activity.start_date:
                errors['start_at'] = (
                    f'A sessão não pode começar antes do início da atividade ({_fmt(activity.start_date)}).'
                )
            if self.end_at and self.end_at > activity.end_date:
                errors.setdefault(
                    'end_at', f'A sessão não pode terminar depois do término da atividade ({_fmt(activity.end_date)}).'
                )
        if errors:
            raise ValidationError(errors)


class RegistrationQuestion(models.Model):
    """Pergunta extra que o participante responde ao se inscrever numa atividade."""

    class Kind(models.TextChoices):
        TEXT = 'TEXT', 'Texto curto'
        LONG_TEXT = 'LONG_TEXT', 'Texto longo'
        CHOICE = 'CHOICE', 'Múltipla escolha'
        YES_NO = 'YES_NO', 'Sim ou não'

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='questions')
    label = models.CharField('Pergunta', max_length=200)
    kind = models.CharField('Tipo de resposta', max_length=20, choices=Kind.choices, default=Kind.TEXT)
    choices = models.TextField('Opções', blank=True, help_text='Só para múltipla escolha: uma opção por linha.')
    required = models.BooleanField('Obrigatória', default=False)
    order = models.PositiveSmallIntegerField('Ordem', default=0)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'pergunta da inscrição'
        verbose_name_plural = 'perguntas da inscrição'

    def __str__(self):
        return self.label

    def choice_list(self):
        return [line.strip() for line in self.choices.splitlines() if line.strip()]

    def clean(self):
        if self.kind == self.Kind.CHOICE and len(self.choice_list()) < 2:
            raise ValidationError({'choices': 'Informe pelo menos duas opções, uma por linha.'})
