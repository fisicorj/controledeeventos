import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


def _fmt(dt):
    return timezone.localtime(dt).strftime('%d/%m/%Y %H:%M')


def unique_slug(model, title, exclude_pk=None, max_length=80):
    """semana-de-engenharia-2026, ou semana-de-engenharia-2026-2 se já existir."""
    base = slugify(title)[: max_length - 4].strip('-') or 'atividade'
    slug, n = base, 2
    qs = model.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug=slug).exists():
        slug, n = f'{base}-{n}', n + 1
    return slug


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
    # Endereço amigável (/eventos/<slug>/). Gerado do título na criação e não muda depois,
    # para não quebrar links já compartilhados.
    slug = models.SlugField('Endereço', max_length=80, unique=True, blank=True)
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
    # Nome que assina os certificados quando não há signatário da instituição (Configurações). Em branco: o nome
    # do organizador. Preenchido na transferência para outro organizador, para os certificados não mudarem.
    signer_name = models.CharField('Assina os certificados', max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    waitlist_enabled = models.BooleanField(
        'Lista de espera', default=True, help_text='Quando lotar, novos interessados entram numa fila.'
    )
    waitlist_offer_hours = models.PositiveSmallIntegerField(
        'Prazo para confirmar a vaga (horas)',
        default=24,
        validators=[MaxValueValidator(168)],
        help_text='Quando abre uma vaga, o primeiro da fila tem esse prazo para confirmar; senão, a vaga passa para '
        'o próximo. 0: confirma na hora, sem perguntar. Perto do início da atividade a confirmação é sempre na hora.',
    )
    certificate_enabled = models.BooleanField(
        'Emitir certificados',
        default=True,
        help_text='Emitidos ao finalizar a atividade, para quem atingir a frequência.',
    )
    workload_hours = models.PositiveSmallIntegerField(
        'Carga horária (horas)', null=True, blank=True, help_text='Aparece no certificado. Opcional.'
    )
    # Inscrição por sessão (registrations/sessions.py): a inscrição continua sendo no evento, com as vagas dele,
    # mas a pessoa escolhe as sessões, cada sessão tem as suas vagas e o certificado sai por sessão.
    per_session = models.BooleanField(
        'Escolha de sessões e certificado por sessão',
        default=False,
        help_text='O participante se inscreve no evento e escolhe as sessões que vai assistir. Cada sessão tem as '
        'suas vagas (com lista de espera) e o certificado é emitido por sessão, para quem teve presença nela.',
    )
    speaker_certificate = models.BooleanField(
        'Certificado para palestrantes',
        default=True,
        help_text='Ao finalizar, cada palestrante cadastrado recebe um certificado, com as sessões em que falou.',
    )
    presenter_certificate = models.BooleanField(
        'Certificado de apresentação de trabalho',
        default=True,
        help_text='Ao finalizar, o autor de cada trabalho aprovado na chamada recebe o certificado de apresentação.',
    )
    presenter_requires_attendance = models.BooleanField(
        'Só com presença do autor',
        default=False,
        help_text='O certificado de apresentação sai só se o autor teve presença registrada na sessão do trabalho '
        '(ou, sem sessão definida, em alguma sessão do evento).',
    )
    min_attendance_percent = models.PositiveSmallIntegerField(
        'Frequência mínima para certificado (%)',
        default=75,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    access_code = models.CharField(
        'Código de acesso',
        max_length=40,
        blank=True,
        help_text='Opcional. Se preenchido, só quem digitar este código consegue se inscrever (evento restrito).',
    )
    previous_edition = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='next_editions',
        verbose_name='Edição anterior',
    )
    survey_enabled = models.BooleanField(
        'Pesquisa de satisfação',
        default=True,
        help_text='Ao finalizar, os participantes recebem um convite para dar uma nota de 0 a 10 e comentar.',
    )
    survey_required = models.BooleanField(
        'Exigir a pesquisa para liberar o certificado',
        default=False,
        help_text='O certificado é emitido, mas o participante só consegue baixá-lo depois de responder.',
    )
    # Monitores e voluntários que fazem check-in nesta atividade, sem acesso ao resto da gestão.
    checkin_staff = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='checkin_activities', verbose_name='Equipe de check-in'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # As mesmas regras do clean(), também no banco: valem para o /admin/, scripts e update() (BD-01).
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gt=F('start_date')), name='activity_end_after_start'),
            models.CheckConstraint(
                condition=Q(registration_end__gt=F('registration_start')), name='activity_registration_period'
            ),
            models.CheckConstraint(
                condition=Q(registration_end__lte=F('end_date')), name='activity_registration_until_end'
            ),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.slug:
            return super().save(*args, **kwargs)
        # Duas atividades com o mesmo título gravadas ao mesmo tempo podem escolher o mesmo endereço (QUA-03):
        # a segunda recebe IntegrityError do banco e tenta de novo com o próximo número livre.
        for attempt in range(5):
            self.slug = unique_slug(Activity, self.title, exclude_pk=self.pk)
            if attempt:
                self.slug = f'{self.slug[:70]}-{secrets.token_hex(2)}'
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                if not Activity.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                    raise  # outro problema, não o endereço repetido
        raise IntegrityError('Não foi possível gerar um endereço único para a atividade.')

    def get_absolute_url(self):
        return reverse('event_page', args=[self.slug])

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
    speakers = models.ManyToManyField('Speaker', blank=True, related_name='sessions', verbose_name='Palestrantes')
    # Só na atividade com inscrição por sessão (Activity.per_session).
    capacity = models.PositiveIntegerField(
        'Vagas da sessão', null=True, blank=True, help_text='Obrigatório quando os participantes escolhem as sessões.'
    )
    workload_hours = models.PositiveSmallIntegerField(
        'Carga horária do certificado (horas)',
        null=True,
        blank=True,
        help_text='Só no certificado por sessão. Em branco: a duração da sessão, arredondada (mínimo 1 hora).',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_at']
        constraints = [models.CheckConstraint(condition=Q(end_at__gt=F('start_at')), name='session_end_after_start')]

    def __str__(self):
        return f'{self.activity.title} - {self.title}'

    @property
    def certificate_hours(self):
        """Carga horária do certificado da sessão: a informada ou a duração arredondada (mínimo 1 hora)."""
        if self.workload_hours:
            return self.workload_hours
        return max(1, round((self.end_at - self.start_at).total_seconds() / 3600))

    def overlaps(self, other):
        return self.start_at < other.end_at and other.start_at < self.end_at

    def clean(self):
        errors = {}
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            errors['end_at'] = 'O término da sessão deve ser posterior ao início.'
        try:
            activity = self.activity
        except Activity.DoesNotExist:
            activity = None
        if activity is not None:
            errors.update(self._capacity_errors(activity))
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

    def _capacity_errors(self, activity):
        if not activity.per_session:
            return {}
        if self.capacity is None:
            return {'capacity': 'Informe as vagas: nesta atividade os participantes escolhem as sessões.'}
        if activity.capacity is not None and self.capacity > activity.capacity:
            return {'capacity': f'Não pode passar das vagas da atividade ({activity.capacity}).'}
        if self.pk:
            taken = self.enrollments.filter(status='CONFIRMED').count()
            if self.capacity < taken:
                return {'capacity': f'Já há {taken} participantes confirmados nesta sessão.'}
        return {}


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


class Speaker(models.Model):
    """Palestrante, professor ou convidado mostrado na página da atividade."""

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='speakers')
    name = models.CharField('Nome', max_length=150)
    role = models.CharField('Cargo ou instituição', max_length=150, blank=True, help_text='Ex.: Professora da Fatec')
    bio = models.TextField('Minibio', blank=True, max_length=600)
    photo = models.ImageField('Foto', upload_to='speakers/', blank=True, null=True)
    link = models.URLField('Link', blank=True, help_text='LinkedIn, Lattes ou site pessoal. Opcional.')
    # Para o certificado de palestrante: o e-mail recebe o PDF (e, se for de uma conta confirmada, o certificado
    # aparece em "Minhas horas"); o documento sai no certificado. Não aparecem na página do evento.
    email = models.EmailField('E-mail', blank=True, help_text='Opcional. Recebe o certificado de palestrante.')
    document = models.CharField(
        'CPF ou matrícula', max_length=50, blank=True, help_text='Opcional. Aparece no certificado de palestrante.'
    )
    order = models.PositiveSmallIntegerField('Ordem', default=0)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'palestrante'
        verbose_name_plural = 'palestrantes'

    def __str__(self):
        return self.name


class Announcement(models.Model):
    """Comunicado da organização aos inscritos, enviado por e-mail e mostrado na página da atividade."""

    class Audience(models.TextChoices):
        CONFIRMED = 'CONFIRMED', 'Inscritos confirmados'
        WAITLISTED = 'WAITLISTED', 'Lista de espera'
        ALL = 'ALL', 'Confirmados e lista de espera'

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='announcements')
    subject = models.CharField('Assunto', max_length=150)
    body = models.TextField('Mensagem', max_length=5000)
    audience = models.CharField('Enviar para', max_length=20, choices=Audience.choices, default=Audience.CONFIRMED)
    show_on_page = models.BooleanField(
        'Mostrar também na página da atividade',
        default=True,
        help_text='Só quem está inscrito vê o aviso na página.',
    )
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    recipients_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'comunicado'
        verbose_name_plural = 'comunicados'

    def __str__(self):
        return self.subject

    def statuses(self):
        if self.audience == self.Audience.ALL:
            return ['CONFIRMED', 'WAITLISTED']
        return [self.audience]


def private_storage():
    """Mantida aqui porque a migração 0005 aponta para ela; a implementação fica em core.storage."""
    from core.storage import private_storage as storage

    return storage()


class SpotCategory(models.Model):
    """Tipo de vaga: ex. 80 para alunos e 20 para público externo, ou vagas reservadas por turma."""

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField('Nome', max_length=80, help_text='Ex.: Alunos, Público externo, Turma EC.8A')
    capacity = models.PositiveIntegerField('Vagas')
    description = models.CharField('Descrição', max_length=200, blank=True)
    access_code = models.CharField(
        'Código de acesso',
        max_length=40,
        blank=True,
        help_text='Opcional. Só quem digitar o código escolhe este tipo de vaga (ex.: código passado à turma).',
    )
    requires_document = models.BooleanField(
        'Só para alunos com matrícula confirmada',
        default=False,
        help_text='Só quem confirmou o e-mail institucional (com a mesma matrícula do cadastro) escolhe esta vaga.',
    )
    order = models.PositiveSmallIntegerField('Ordem', default=0)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'tipo de vaga'
        verbose_name_plural = 'tipos de vaga'
        constraints = [models.UniqueConstraint(fields=['activity', 'name'], name='unique_category_name')]

    def __str__(self):
        return self.name

    def clean(self):
        try:
            activity = self.activity
        except Activity.DoesNotExist:
            return
        if self.capacity is not None and activity.capacity is not None and self.capacity > activity.capacity:
            raise ValidationError({'capacity': f'Não pode passar das vagas da atividade ({activity.capacity}).'})


class Material(models.Model):
    """Slides, apostilas e gravações liberados depois do evento só para quem participou."""

    class Audience(models.TextChoices):
        ATTENDED = 'ATTENDED', 'Quem teve presença registrada'
        CONFIRMED = 'CONFIRMED', 'Todos os inscritos confirmados'

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name='materials')
    title = models.CharField('Título', max_length=150)
    description = models.CharField('Descrição', max_length=300, blank=True)
    file = models.FileField('Arquivo', upload_to='materials/', storage=private_storage, blank=True)
    url = models.URLField('Link', blank=True, help_text='Gravação no YouTube, pasta no Drive/OneDrive etc.')
    audience = models.CharField('Quem pode acessar', max_length=20, choices=Audience.choices, default=Audience.ATTENDED)
    available_from = models.DateTimeField(
        'Liberar a partir de', null=True, blank=True, help_text='Em branco: liberado assim que a atividade terminar.'
    )
    order = models.PositiveSmallIntegerField('Ordem', default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'material'
        verbose_name_plural = 'materiais'

    def __str__(self):
        return self.title

    def clean(self):
        if bool(self.file) == bool(self.url):
            raise ValidationError('Envie um arquivo ou informe um link (um dos dois).')

    def release_at(self):
        return self.available_from or self.activity.end_date

    def is_released(self):
        return timezone.now() >= self.release_at()
