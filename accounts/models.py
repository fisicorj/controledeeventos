from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.db.models import ProtectedError, Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    class Role(models.TextChoices):
        PARTICIPANT = 'PARTICIPANT', 'Participante'
        ORGANIZER = 'ORGANIZER', 'Organizador'
        ADMIN = 'ADMIN', 'Administrador'

    # Único só entre contas confirmadas (constraint abaixo): uma conta que nunca confirmou o código não segura o
    # endereço, e o cadastro com um e-mail já em uso não revela isso a quem tenta (ver accounts/verification.py).
    email = models.EmailField(_('email address'))
    role = models.CharField('Papel', max_length=20, choices=Role.choices, default=Role.PARTICIPANT)
    # Matrícula (aluno) ou CPF (convidado), digitado pelo próprio usuário e validado em accounts/documents.py.
    # A matrícula só vale como "de aluno" quando bate com o e-mail institucional confirmado (student_id_confirmed).
    institutional_id = models.CharField('Matrícula ou CPF', max_length=50, blank=True)
    # Contas criadas pelo cadastro começam sem confirmação; as criadas pelo administrador já valem.
    email_verified = models.BooleanField('E-mail confirmado', default=True)
    # Quando a pessoa concordou com o aviso de privacidade (no cadastro). Contas antigas ficam em branco.
    privacy_accepted_at = models.DateTimeField('Aceite do aviso de privacidade', null=True, blank=True)

    class Meta(AbstractUser.Meta):
        swappable = 'AUTH_USER_MODEL'
        constraints = [
            models.UniqueConstraint(
                Lower('email'),
                condition=Q(email_verified=True),
                name='unique_verified_email',
                violation_error_message='Já existe uma conta confirmada com este e-mail.',
            )
        ]

    def __str__(self):
        return self.get_full_name() or self.username

    def student_id_confirmed_in(self, site):
        """Matrícula confirmada: e-mail confirmado, do domínio dos alunos, e igual à matrícula digitada."""
        document = (self.institutional_id or '').strip().lower()
        return bool(self.email_verified and document and site.student_id_from_email(self.email) == document)

    @property
    def student_id_confirmed(self):
        from core.models import SiteSettings

        return self.student_id_confirmed_in(SiteSettings.load())


def email_taken(email, exclude_pk=None):
    """O e-mail pertence a uma conta confirmada? Conta que nunca confirmou o código não segura o endereço:
    senão qualquer pessoa poderia cadastrar o e-mail institucional de um colega e travar o cadastro dele."""
    accounts = User.objects.filter(email__iexact=email, email_verified=True)
    if exclude_pk is not None:
        accounts = accounts.exclude(pk=exclude_pk)
    return accounts.exists()


def release_email(email, exclude_pk=None):
    """Apaga as contas não confirmadas que seguram este e-mail. Só o dono da caixa de entrada confirma,
    então quem chega com o código (ou com o link de nova senha) fica com o endereço.
    Devolve False se alguma conta não pôde ser apagada (já tem histórico ligado a ela)."""
    accounts = User.objects.filter(email__iexact=email, email_verified=False)
    if exclude_pk is not None:
        accounts = accounts.exclude(pk=exclude_pk)
    for account in accounts:
        try:
            with transaction.atomic():
                account.delete()
        except ProtectedError:
            return False
    return True


class EmailCode(models.Model):
    """Código de 6 dígitos enviado para confirmar um e-mail (no cadastro ou ao trocar de e-mail)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_codes')
    email = models.EmailField()
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    # Matrícula/CPF digitada junto com a troca de e-mail: só é gravada quando o novo e-mail for confirmado.
    # None = não mexer no que está no cadastro.
    institutional_id = models.CharField(max_length=50, null=True, blank=True)  # noqa: DJ001 (None tem sentido)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.email} ({self.created_at:%d/%m %H:%M})'


class MfaDevice(models.Model):
    """Verificação em duas etapas por aplicativo autenticador (TOTP, RFC 6238). Ver accounts/mfa.py."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='mfa_device')
    # Segredo criptografado com a chave derivada da SECRET_KEY (core/crypto.py).
    secret = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    # Preenchido quando a pessoa digita o primeiro código certo; antes disso o cadastro não vale.
    confirmed_at = models.DateTimeField('Ativada em', null=True, blank=True)
    # Último intervalo de 30 segundos aceito: o mesmo código não serve duas vezes.
    last_step = models.BigIntegerField(default=0)
    # Hashes (HMAC) dos códigos de recuperação ainda não usados.
    recovery_codes = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = 'verificação em duas etapas'
        verbose_name_plural = 'verificações em duas etapas'

    def __str__(self):
        return f'{self.user} ({"ativa" if self.confirmed_at else "não ativada"})'
