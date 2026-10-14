"""SEC-01: destinatário e conteúdo dos e-mails enviados não ficam guardados para sempre."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.privacy import anonymize
from activities.test_utils import make_user
from core import outbox
from core.models import OutgoingEmail

Status = OutgoingEmail.Status


def email(to='ana@exemplo.com', status=Status.SENT, days_ago=0, group='g'):
    sent_at = timezone.now() - timedelta(days=days_ago) if status == Status.SENT else None
    return OutgoingEmail.objects.create(
        to=to, subject='Assunto', body='Olá Ana', status=status, sent_at=sent_at, group=group,
        attachments=[{'kind': 'qr', 'id': 1}], ip='10.0.0.1',
    )  # fmt: skip


class RetentionTests(TestCase):
    def test_apaga_so_o_conteudo_dos_enviados_antigos(self):
        old, recent = email(days_ago=31), email(days_ago=29)
        pending = email(status=Status.PENDING)
        self.assertEqual(outbox.redact_sent(30), 1)
        old.refresh_from_db()
        self.assertEqual((old.to, old.subject, old.body, old.attachments, old.ip), ('', '', '', [], None))
        self.assertEqual(old.status, Status.SENT)  # a contagem do grupo continua certa
        self.assertEqual(outbox.group_status('g')['g'], {'pending': 1, 'sent': 2, 'failed': 0})
        for row in (recent, pending):
            row.refresh_from_db()
            self.assertEqual(row.body, 'Olá Ana')
        self.assertEqual(outbox.redact_sent(30), 0)  # não repete

    @override_settings(OUTBOX_RETENTION_DAYS=0)
    def test_prazo_zero_desliga(self):
        email(days_ago=400)
        self.assertEqual(outbox.redact_sent(), 0)
        out = StringIO()
        call_command('purge_outbox', stdout=out)
        self.assertIn('desligada', out.getvalue())

    @override_settings(OUTBOX_RETENTION_DAYS=10)
    def test_comando_usa_o_prazo_configurado(self):
        email(days_ago=11)
        out = StringIO()
        call_command('purge_outbox', stdout=out)
        self.assertIn('10 dia(s)', out.getvalue())
        self.assertIn(': 1.', out.getvalue())
        self.assertEqual(OutgoingEmail.objects.get().body, '')

    def test_worker_faz_a_limpeza(self):
        from core.management.commands.process_outbox import Command

        email(days_ago=40)
        out = StringIO()
        Command(stdout=out).upkeep()
        self.assertIn('conteúdo apagado: 1', out.getvalue())

    def test_exclusao_de_conta_limpa_os_enviados_da_pessoa(self):
        ana = make_user('ana')
        sent = email(to='ANA@exemplo.com')
        email(status=Status.PENDING)
        email(status=Status.FAILED)
        other = email(to='bia@exemplo.com')
        anonymize(ana)
        self.assertEqual(OutgoingEmail.objects.filter(status__in=[Status.PENDING, Status.FAILED]).count(), 0)
        sent.refresh_from_db()
        self.assertEqual((sent.to, sent.body, sent.status), ('', '', Status.SENT))
        other.refresh_from_db()
        self.assertEqual(other.body, 'Olá Ana')


class FailedEmailsTests(TestCase):
    """SEC-15 da 2ª auditoria: falhas também têm prazo, e só as recentes voltam para a fila."""

    def failed(self, days_ago):
        row = email(status=Status.FAILED)
        OutgoingEmail.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return row

    def test_falha_antiga_tem_o_conteudo_apagado(self):
        old, recent = self.failed(40), self.failed(1)
        self.assertEqual(outbox.redact_sent(30), 1)
        old.refresh_from_db()
        recent.refresh_from_db()
        self.assertEqual((old.to, old.body, old.status), ('', '', Status.FAILED))
        self.assertEqual(recent.body, 'Olá Ana')

    @override_settings(OUTBOX_RETRY_DAYS=3)
    def test_tentar_de_novo_so_reenvia_as_recentes(self):
        old, recent = self.failed(10), self.failed(1)
        self.assertEqual(outbox.retry_failed(), 1)
        old.refresh_from_db()
        recent.refresh_from_db()
        self.assertEqual(old.status, Status.FAILED)
        self.assertIn(recent.status, (Status.PENDING, Status.SENT))

    def test_tela_mostra_quantas_podem_voltar(self):
        from django.urls import reverse

        from accounts.models import User

        self.failed(10)
        self.failed(1)
        admin = User.objects.create_superuser('root', 'root@exemplo.com', 'Senha!forte123')
        self.client.force_login(admin)
        page = self.client.get(reverse('email_settings'))
        self.assertContains(page, 'Tentar de novo as falhas dos últimos 3 dias (1)')
        self.assertContains(page, 'Falhas com mais de 3 dias não são reenviadas')
