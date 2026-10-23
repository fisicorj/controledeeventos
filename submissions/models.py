"""Chamada de trabalhos de um evento: submissão, avaliação (às cegas ou aberta) e decisão.

A chamada pertence a uma atividade já cadastrada e só aparece com ela publicada. O trabalho aceito entra na
programação do evento e o autor que submeteu fica inscrito automaticamente (submissions/services.py).
"""

import re
import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from activities.models import Activity, Session
from core.storage import private_storage

EMAIL_IN_LINE = re.compile(r'<?\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b>?')
EMAIL_LABEL = re.compile(r'[\s,;:|–-]*\be-?mail\b[\s:]*$', re.IGNORECASE)  # "Ana - e-mail: ana@..." sobra "e-mail:"


def parse_coauthor(line):
    """'Maria Souza (Fatec) maria@exemplo.com' -> ('Maria Souza (Fatec)', 'maria@exemplo.com').
    O e-mail é opcional e serve só para o certificado de apresentação: nunca aparece nas listas."""
    found = EMAIL_IN_LINE.findall(line)
    email = found[0].strip('<>').lower() if len(found) == 1 else ''
    name = ' '.join(EMAIL_IN_LINE.sub(' ', line).split())
    name = EMAIL_LABEL.sub('', name).strip(' -–,;:|')
    return name, email


class CallForPapers(models.Model):
    activity = models.OneToOneField(Activity, on_delete=models.CASCADE, related_name='call_for_papers')
    opens_at = models.DateTimeField('Submissões a partir de')
    closes_at = models.DateTimeField('Submissões até')
    instructions = models.TextField(
        'Instruções aos autores',
        help_text='Formato, número de páginas, modelo a seguir, o que acontece depois da aprovação etc.',
    )
    areas = models.TextField(
        'Áreas temáticas', blank=True, help_text='Opcional. Uma por linha; o autor escolhe uma ao submeter.'
    )
    blind_review = models.BooleanField(
        'Avaliação às cegas',
        default=True,
        help_text='Os avaliadores não veem quem são os autores. Peça aos autores que tirem os nomes do arquivo.',
    )
    max_authors = models.PositiveSmallIntegerField(
        'Máximo de autores por trabalho', default=6, validators=[MinValueValidator(1), MaxValueValidator(20)]
    )
    max_submissions = models.PositiveSmallIntegerField(
        'Máximo de trabalhos por autor',
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text='Quantos trabalhos cada pessoa pode enviar nesta chamada (os retirados não contam).',
    )
    reviewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='review_calls', verbose_name='Avaliadores'
    )
    # Coordenação da chamada (comissão científica): cuidam só da chamada deste evento (avaliadores, distribuição,
    # decisões), sem acesso às inscrições nem à gestão da atividade. Quem inclui e tira é a organização do evento.
    coordinators = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='coordinated_calls', verbose_name='Coordenação da chamada'
    )
    criteria = models.TextField(
        'Critérios de avaliação',
        blank=True,
        help_text='Opcional. Um por linha (ex.: Originalidade, Metodologia, Clareza). Cada critério recebe uma nota '
        'de 1 a 5, e a nota do trabalho é a média deles. Em branco: uma nota única.',
    )
    review_days = models.PositiveSmallIntegerField(
        'Prazo para avaliar (dias)',
        default=14,
        validators=[MinValueValidator(1), MaxValueValidator(90)],
        help_text='Contado a partir da designação (ou da nova versão). Dois dias antes do fim, o avaliador com '
        'avaliação pendente recebe um lembrete; depois do prazo, mais um a cada 3 dias (no máximo 3 no total).',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'chamada de trabalhos'
        verbose_name_plural = 'chamadas de trabalhos'
        constraints = [models.CheckConstraint(condition=Q(closes_at__gt=F('opens_at')), name='call_closes_after_open')]

    def __str__(self):
        return f'Chamada de trabalhos: {self.activity}'

    def area_list(self):
        return [line.strip() for line in self.areas.splitlines() if line.strip()]

    def criteria_list(self):
        seen, items = set(), []
        for line in self.criteria.splitlines():
            name = line.strip()[:80]
            if name and name.lower() not in seen:
                seen.add(name.lower())
                items.append(name)
        return items

    @property
    def is_visible(self):
        """A chamada só aparece com o evento publicado."""
        return self.activity.status == Activity.Status.PUBLISHED

    @property
    def is_open(self):
        now = timezone.now()
        return self.is_visible and self.opens_at <= now <= self.closes_at


def submission_path(instance, filename):
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    return f'submissions/{instance.call_id}/{secrets.token_hex(12)}.{extension}'


class Submission(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = 'SUBMITTED', 'Enviado'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Em avaliação'
        REVISION = 'REVISION', 'Correções solicitadas'
        ACCEPTED = 'ACCEPTED', 'Aprovado'
        REJECTED = 'REJECTED', 'Não aprovado'
        WITHDRAWN = 'WITHDRAWN', 'Retirado pelo autor'

    FINAL = (Status.ACCEPTED, Status.REJECTED, Status.WITHDRAWN)

    call = models.ForeignKey(CallForPapers, on_delete=models.CASCADE, related_name='submissions')
    # Quem submeteu: é o autor de contato e quem fica inscrito no evento quando o trabalho é aprovado.
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='submissions')
    title = models.CharField('Título', max_length=250)
    abstract = models.TextField('Resumo', max_length=5000)
    keywords = models.CharField('Palavras-chave', max_length=250, blank=True, help_text='Separadas por vírgula.')
    area = models.CharField('Área temática', max_length=150, blank=True)
    coauthors = models.TextField(
        'Coautores',
        blank=True,
        help_text=(
            'Um por linha, com a instituição. Ex.: Maria Souza (Fatec Ipiranga) maria@exemplo.com. Você já conta '
            'como autor. O e-mail é opcional: com ele, o coautor também recebe o certificado de apresentação '
            '(o e-mail não aparece para os avaliadores nem na lista de trabalhos).'
        ),
    )
    file = models.FileField('Arquivo do trabalho', upload_to=submission_path, storage=private_storage)
    file_name = models.CharField(max_length=200, blank=True)  # nome original, para mostrar
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    version = models.PositiveSmallIntegerField(default=1)
    decision_note = models.TextField('Mensagem ao autor', blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+'
    )
    # Trabalho aprovado: em qual sessão da programação será apresentado (opcional).
    session = models.ForeignKey(
        Session, on_delete=models.SET_NULL, null=True, blank=True, related_name='works', verbose_name='Sessão'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'trabalho submetido'
        verbose_name_plural = 'trabalhos submetidos'

    def __str__(self):
        return self.title

    def coauthor_entries(self):
        """[(nome, e-mail ou '')] na ordem informada."""
        return [parse_coauthor(line) for line in self.coauthors.splitlines() if line.strip()]

    def coauthor_list(self):
        return [name for name, _ in self.coauthor_entries()]

    @property
    def author_names(self):
        main = self.author.get_full_name() or self.author.username
        return ', '.join([main, *self.coauthor_list()])

    @property
    def author_can_edit(self):
        """O autor troca o arquivo e os dados enquanto ninguém avaliou (no prazo) ou quando pediram correções."""
        if self.status == self.Status.REVISION:
            return self.call.is_visible
        return self.status == self.Status.SUBMITTED and self.call.is_open

    @property
    def is_final(self):
        return self.status in self.FINAL


class Review(models.Model):
    """Avaliação de um trabalho por um avaliador. Criada vazia na designação; submitted_at marca a entrega."""

    class Recommendation(models.TextChoices):
        ACCEPT = 'ACCEPT', 'Aprovar'
        REVISION = 'REVISION', 'Aprovar com correções'
        REJECT = 'REJECT', 'Não aprovar'

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name='reviews')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='reviews')
    recommendation = models.CharField('Recomendação', max_length=20, choices=Recommendation.choices, blank=True)
    score = models.PositiveSmallIntegerField(
        'Nota (1 a 5)', null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comments_to_author = models.TextField(
        'Comentários ao autor', blank=True, max_length=8000, help_text='O autor vê, sem saber quem avaliou.'
    )
    comments_to_organizer = models.TextField(
        'Comentários só para a organização', blank=True, max_length=4000, help_text='O autor não vê.'
    )
    version = models.PositiveSmallIntegerField(default=1)  # versão do trabalho avaliada
    # Com critérios na chamada: {critério: nota de 1 a 5}; score fica com a média arredondada.
    criteria_scores = models.JSONField(default=dict, blank=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    # Prazo da avaliação (designação ou nova versão + review_days da chamada) e lembretes enviados.
    due_at = models.DateTimeField('Prazo', null=True, blank=True)
    reminders_sent = models.PositiveSmallIntegerField(default=0)
    reminded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['assigned_at']
        verbose_name = 'avaliação'
        verbose_name_plural = 'avaliações'
        constraints = [models.UniqueConstraint(fields=['submission', 'reviewer'], name='unique_review_per_reviewer')]

    def __str__(self):
        return f'{self.reviewer} - {self.submission}'

    @property
    def is_done(self):
        return self.submitted_at is not None

    @property
    def is_overdue(self):
        return not self.is_done and self.due_at is not None and self.due_at < timezone.now()


class ReviewerAreas(models.Model):
    """Áreas temáticas que um avaliador aceita numa chamada (vazio: todas). Usado na distribuição automática."""

    call = models.ForeignKey(CallForPapers, on_delete=models.CASCADE, related_name='reviewer_areas')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    areas = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['call', 'reviewer'], name='unique_reviewer_areas')]

    def __str__(self):
        return f'{self.reviewer} - {self.call}'

    def area_list(self):
        return [line.strip() for line in self.areas.splitlines() if line.strip()]


class SubmissionEvent(models.Model):
    """Histórico das decisões da organização sobre um trabalho (decidir e reabrir), com quem e por quê."""

    class Action(models.TextChoices):
        DECIDED = 'DECIDED', 'Decisão'
        REOPENED = 'REOPENED', 'Avaliação reaberta'

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name='events')
    action = models.CharField(max_length=20, choices=Action.choices)
    status_before = models.CharField(max_length=20, choices=Submission.Status.choices)
    status_after = models.CharField(max_length=20, choices=Submission.Status.choices)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'histórico do trabalho'
        verbose_name_plural = 'históricos dos trabalhos'

    def __str__(self):
        return f'{self.get_action_display()}: {self.submission}'


class ReviewDecline(models.Model):
    """O avaliador recusou a designação (conflito de interesse ou falta de tempo). A avaliação pendente sai, e a
    distribuição automática não designa de novo a mesma pessoa para o mesmo trabalho."""

    class Reason(models.TextChoices):
        CONFLICT = 'CONFLICT', 'Conflito de interesse'
        UNAVAILABLE = 'UNAVAILABLE', 'Sem disponibilidade no prazo'
        EXPERTISE = 'EXPERTISE', 'Fora da minha área'

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name='declines')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    reason = models.CharField('Motivo', max_length=20, choices=Reason.choices)
    note = models.TextField('Observação', blank=True, max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'recusa de avaliação'
        verbose_name_plural = 'recusas de avaliação'

    def __str__(self):
        return f'{self.reviewer} recusou {self.submission}'


class ReviewerInvite(models.Model):
    """Convite por e-mail para avaliar numa chamada, para quem ainda não tem conta (submissions/invites.py).
    O link só leva o token; no banco fica o hash dele."""

    call = models.ForeignKey(CallForPapers, on_delete=models.CASCADE, related_name='reviewer_invites')
    email = models.EmailField('E-mail')
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'convite de avaliador'
        verbose_name_plural = 'convites de avaliador'

    def __str__(self):
        return f'{self.email} ({self.call})'

    @property
    def is_open(self):
        return not self.accepted_at and not self.cancelled_at and self.expires_at > timezone.now()
