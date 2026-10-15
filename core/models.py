from contextvars import ContextVar
from email.utils import formataddr

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from .crypto import decrypt, encrypt
from .storage import private_storage

# Durante uma requisição, as configurações são lidas do banco uma vez só (antes eram várias leituras por página
# e uma por e-mail montado). O middleware core.middleware.RequestCacheMiddleware abre e fecha esse cache.
# Fora de requisição (comandos, fila de e-mails), não há cache: cada leitura vai ao banco.
request_cache = ContextVar('arena_request_cache', default=None)


class SingletonModel(models.Model):
    """Modelo com um único registro (pk=1), criado automaticamente na primeira leitura."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache = request_cache.get()
        if cache is not None and type(self) in cache:
            cache[type(self)] = self  # quem já leu nesta requisição passa a ver o que foi gravado

    def delete(self, *args, **kwargs):
        pass  # o registro único nunca é apagado

    @classmethod
    def load(cls, fresh=False):
        """fresh=True: cópia própria, para formulários (um formulário inválido altera o objeto que recebeu)."""
        cache = None if fresh else request_cache.get()
        if cache is not None and cls in cache:
            return cache[cls]
        obj, _ = cls.objects.get_or_create(pk=1)
        if cache is not None:
            cache[cls] = obj
        return obj


HEX_COLOR = RegexValidator(r'^#[0-9A-Fa-f]{6}$', 'Use uma cor no formato #RRGGBB.')


class SiteSettings(SingletonModel):
    """Configurações da instituição, editadas pelos administradores dentro da plataforma.

    Existe um único registro (pk=1), criado automaticamente na primeira leitura.
    """

    portal_name = models.CharField(
        'Nome do portal',
        max_length=60,
        default='Arena+',
        help_text='Aparece no topo do site, ao lado do logo, e no título das páginas.',
    )
    primary_color = models.CharField(
        'Cor principal',
        max_length=7,
        default='#0B4F8A',
        validators=[HEX_COLOR],
        help_text='Usada em botões, links e destaques. Prefira uma cor escura da identidade da instituição.',
    )
    institution_name = models.CharField(
        'Nome da instituição',
        max_length=150,
        default='Arena+',
        help_text='Aparece no topo dos certificados e relatórios e no assunto dos e-mails.',
    )
    city = models.CharField(
        'Cidade',
        max_length=100,
        blank=True,
        default='São Paulo',
        help_text='Usada na linha de data do certificado ("São Paulo, 2 de outubro de 2026."). Opcional.',
    )
    logo = models.ImageField(
        'Logo',
        upload_to='institution/',
        blank=True,
        help_text='PNG com fundo transparente (ou JPG). Aparece no topo do site e no certificado.',
    )
    signer_name = models.CharField(
        'Quem assina os certificados',
        max_length=150,
        blank=True,
        help_text='Ex.: Profa. Maria Souza. Em branco, assina o organizador de cada atividade.',
    )
    signer_role = models.CharField(
        'Cargo de quem assina',
        max_length=150,
        blank=True,
        help_text='Ex.: Diretora Acadêmica. Em branco, aparece "Organização, <nome da instituição>".',
    )
    certificate_background = models.ImageField(
        'Arte de fundo do certificado',
        upload_to='institution/',
        storage=private_storage,
        blank=True,
        help_text='Imagem A4 deitada (proporção 297 × 210, ex.: 3508 × 2480 px) com a moldura e os logos. '
        'O texto é escrito por cima. Em branco, usa o modelo padrão do sistema.',
    )
    certificate_title = models.CharField(
        'Título do certificado',
        max_length=40,
        blank=True,
        default='CERTIFICADO',
        help_text='Ex.: CERTIFICADO ou DECLARAÇÃO. Deixe em branco se a arte de fundo já tiver o título.',
    )
    certificate_opening = models.CharField(
        'Frase de abertura', max_length=60, default='Certificamos que', help_text='Ex.: Declaramos que'
    )
    certificate_place = models.CharField(
        'Local de realização',
        max_length=200,
        blank=True,
        help_text='Como aparece depois de “realizado”. Ex.: na Faculdade Engenheiro Salvador Arena. '
        'Em branco, essa linha não aparece.',
    )
    document_label = models.CharField(
        'Rótulo da matrícula',
        max_length=20,
        default='Matrícula',
        help_text='Aparece no canto superior direito, antes do número informado no cadastro. Ex.: RA',
    )
    certificate_offset_mm = models.SmallIntegerField(
        'Ajuste horizontal do texto (mm)',
        default=0,
        validators=[MinValueValidator(-60), MaxValueValidator(60)],
        help_text='Desloca o bloco de texto para a direita (positivo) ou esquerda (negativo), '
        'para centralizá-lo na área livre da arte.',
    )
    certificate_show_qr = models.BooleanField(
        'Mostrar QR Code de validação',
        default=True,
        help_text='Aparece pequeno no canto inferior direito e abre a página que confirma a autenticidade.',
    )
    signature_image = models.ImageField(
        'Imagem da assinatura',
        upload_to='institution/',
        storage=private_storage,
        blank=True,
        help_text='Opcional. PNG com fundo transparente fica melhor. Só é usada junto com "Quem assina".',
    )
    student_email_domains = models.CharField(
        'Domínio do e-mail dos alunos',
        max_length=200,
        blank=True,
        help_text='Ex.: aluno.fsa.br (vários separados por vírgula). A matrícula digitada no cadastro precisa ser '
        'igual ao que vem antes do @ e só vale depois que o código enviado para o e-mail é confirmado. '
        'Em branco, ninguém conta como aluno com matrícula confirmada.',
    )
    privacy_contact_email = models.EmailField(
        'E-mail para assuntos de privacidade',
        blank=True,
        help_text='Aparece no aviso de privacidade (LGPD) como o canal para pedidos sobre dados pessoais. '
        'Ex.: o e-mail do encarregado de dados (DPO) da instituição.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'configurações da instituição'
        verbose_name_plural = 'configurações da instituição'

    def __str__(self):
        return 'Configurações da instituição'

    def student_domains(self):
        return [d.strip().lower().lstrip('@') for d in self.student_email_domains.split(',') if d.strip()]

    def student_id_from_email(self, email):
        """Matrícula a partir do e-mail institucional (12546456@aluno.fsa.br vira 12546456), ou ''."""
        local, _, domain = (email or '').strip().lower().rpartition('@')
        return local if local and domain in self.student_domains() else ''

    DEFAULT_COLOR = '#0B4F8A'

    def _rgb(self):
        value = (self.primary_color or '').lstrip('#')
        try:
            return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4)) if len(value) == 6 else self._default_rgb()
        except ValueError:
            return self._default_rgb()

    def _default_rgb(self):
        value = self.DEFAULT_COLOR.lstrip('#')
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))

    @property
    def safe_primary_color(self):
        return '#{:02X}{:02X}{:02X}'.format(*self._rgb())

    @property
    def primary_rgb(self):
        return ', '.join(str(c) for c in self._rgb())

    @property
    def primary_contrast(self):
        """Cor do texto sobre a cor principal: branco em cores escuras, quase preto em cores claras."""
        r, g, b = (c / 255 for c in self._rgb())

        def channel(c):
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
        return '#ffffff' if luminance < 0.4 else '#1c2533'


class EmailSettings(SingletonModel):
    """Servidor SMTP/relay usado para enviar os e-mails da plataforma."""

    class Security(models.TextChoices):
        STARTTLS = 'STARTTLS', 'STARTTLS (normalmente porta 587)'
        SSL = 'SSL', 'SSL/TLS direto (normalmente porta 465)'
        NONE = 'NONE', 'Nenhuma: relay interno sem criptografia (normalmente porta 25)'

    DEFAULT_PORTS = {Security.STARTTLS: 587, Security.SSL: 465, Security.NONE: 25}

    enabled = models.BooleanField(
        'Enviar e-mails pelo servidor SMTP',
        default=False,
        help_text='Desligado, nenhum e-mail sai: as mensagens aparecem só no log/terminal (útil em desenvolvimento).',
    )
    host = models.CharField('Servidor (host)', max_length=255, blank=True, help_text='Ex.: smtp-relay.instituicao.br')
    port = models.PositiveIntegerField('Porta', default=587)
    security = models.CharField('Segurança', max_length=10, choices=Security.choices, default=Security.STARTTLS)
    verify_certificate = models.BooleanField(
        'Verificar o certificado do servidor',
        default=True,
        help_text='Desmarque apenas para relay interno com certificado autoassinado.',
    )
    username = models.CharField(
        'Usuário',
        max_length=255,
        blank=True,
        help_text='Deixe em branco se o relay não pede autenticação (liberação pelo IP do servidor).',
    )
    password_encrypted = models.TextField(blank=True, editable=False)
    from_email = models.EmailField(
        'E-mail do remetente',
        blank=True,
        help_text='Ex.: nao-responda@instituicao.br. O relay precisa aceitar envios com este endereço.',
    )
    from_name = models.CharField(
        'Nome do remetente', max_length=150, blank=True, help_text='Em branco, usa o nome da instituição.'
    )
    reply_to = models.EmailField(
        'Responder para', blank=True, help_text='Opcional. Para onde vão as respostas dos participantes.'
    )
    timeout = models.PositiveSmallIntegerField('Tempo limite de conexão (segundos)', default=15)
    error_alert_email = models.EmailField(
        'E-mail para alertas de erro',
        blank=True,
        help_text='Recebe um aviso quando o portal dá erro (página "Erro 500"), com o endereço e o detalhe técnico. '
        'No máximo 10 avisos por hora.',
    )
    last_test_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_test_ok = models.BooleanField(null=True, editable=False)
    last_test_message = models.CharField(max_length=500, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'configuração de e-mail'
        verbose_name_plural = 'configuração de e-mail'

    def __str__(self):
        return 'Configuração de e-mail'

    @property
    def has_password(self):
        return bool(self.password_encrypted)

    def set_password(self, raw):
        self.password_encrypted = encrypt(raw)

    def get_password(self):
        return decrypt(self.password_encrypted)

    def sender(self):
        if not self.from_email:
            return None
        name = self.from_name or SiteSettings.load().institution_name
        return formataddr((name, self.from_email))

    def clean(self):
        errors = {}
        if self.enabled:
            if not self.host:
                errors['host'] = 'Informe o servidor para ligar o envio.'
            if not self.from_email:
                errors['from_email'] = 'Informe o e-mail do remetente para ligar o envio.'
        if self.username and self.security == self.Security.NONE:
            errors['security'] = (
                'Com usuário e senha, use STARTTLS ou SSL/TLS: sem criptografia a senha trafegaria aberta na rede.'
            )
        if errors:
            raise ValidationError(errors)


class Throttle(models.Model):
    """Contador de tentativas por chave (hash) e janela de tempo. Ver core/throttle.py."""

    key = models.CharField(max_length=64, unique=True)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'limite de tentativas'
        verbose_name_plural = 'limites de tentativas'

    def __str__(self):
        return f'{self.key[:12]}… ({self.count})'


class OutgoingEmail(models.Model):
    """Fila de e-mails. Os avisos em massa (finalizar atividade, comunicados, certificados avulsos) entram aqui
    na mesma transação da operação e são enviados fora da requisição (ver core/outbox.py). Assim um lote grande
    não estoura o tempo limite do servidor, e quem já recebeu não recebe de novo."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Na fila'
        SENDING = 'SENDING', 'Enviando'
        SENT = 'SENT', 'Enviado'
        FAILED = 'FAILED', 'Falhou'

    created_at = models.DateTimeField('Criado em', auto_now_add=True)
    to = models.EmailField('Para')
    subject = models.CharField('Assunto', max_length=300)
    body = models.TextField('Mensagem')
    reply_to = models.EmailField('Responder para', blank=True)
    # Anexos gerados na hora do envio: [{"kind": "qr" | "ics" | "certificate_pdf", "id": 123}]
    attachments = models.JSONField('Anexos', default=list, blank=True)
    # Agrupa os envios de uma mesma ação (ex.: "announcement:5", "batch:3") para mostrar o andamento.
    group = models.CharField('Grupo', max_length=60, blank=True, db_index=True)
    # Certificado avulso enviado por este e-mail: ao sair, marca o envio e registra na auditoria.
    certificate_pk = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    actor_pk = models.PositiveBigIntegerField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField('Situação', max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField('Tentativas', default=0)
    next_try_at = models.DateTimeField('Próxima tentativa', default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField('Enviado em', null=True, blank=True)
    last_error = models.CharField('Último erro', max_length=500, blank=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['status', 'next_try_at'], name='outbox_queue_idx')]
        verbose_name = 'e-mail na fila'
        verbose_name_plural = 'fila de e-mails'

    def __str__(self):
        return f'{self.to}: {self.subject}'


class MaintenanceRun(models.Model):
    """Uma execução da faxina diária (core/maintenance.py), para a tela Configurações > Manutenção."""

    KEEP = 60  # execuções guardadas; as mais antigas saem

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=200, default='comando')
    results = models.JSONField(default=dict)  # {descrição: quantidade ou texto}
    failed = models.BooleanField(default=False)
    # Pedida pelo botão "Rodar agora": espera o worker (ou a linha de execução do servidor) pegar (DES-05).
    queued = models.BooleanField(default=False)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'Faxina de {timezone.localtime(self.started_at):%d/%m/%Y %H:%M}'
