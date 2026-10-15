"""Configurações > Usuários: painel de contas e permissões (accounts/user_admin.py)."""

from django.contrib.sessions.models import Session
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse

from activities.test_utils import PASSWORD, make_user

from .models import EmailCode, MfaDevice, User, UserChange
from .test_reauth import expire_reauth


class UserAdminTests(TestCase):
    def setUp(self):
        self.admin = make_user('chefe', User.Role.ADMIN, first_name='Carla', last_name='Chefe')
        self.ana = make_user('ana', first_name='Ana', last_name='Lima', email_verified=True)
        self.client.force_login(self.admin)

    def post(self, name, user, data=None):
        return self.client.post(reverse(name, args=[user.pk]), data or {})

    def detail(self, user):
        return reverse('user_admin_detail', args=[user.pk])

    def test_so_administrador(self):
        self.client.force_login(make_user('org', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(reverse('user_admin_list')).status_code, 403)
        self.assertEqual(self.client.get(self.detail(self.ana)).status_code, 403)
        self.assertEqual(self.post('user_admin_role', self.ana, {'role': 'ADMIN'}).status_code, 403)
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.role, User.Role.PARTICIPANT)

    def test_lista_busca_e_filtros(self):
        make_user('bia', User.Role.ORGANIZER, first_name='Bia', email_verified=False)
        page = self.client.get(reverse('user_admin_list'), {'q': 'lima'})
        self.assertEqual([u.username for u in page.context['page'].object_list], ['ana'])
        page = self.client.get(reverse('user_admin_list'), {'papel': 'ORGANIZER', 'email': 'pendente'})
        self.assertEqual([u.username for u in page.context['page'].object_list], ['bia'])
        MfaDevice.objects.create(user=self.ana, secret='x', confirmed_at='2026-01-01T00:00Z')
        page = self.client.get(reverse('user_admin_list'), {'duas_etapas': 'sim'})
        self.assertEqual([u.username for u in page.context['page'].object_list], ['ana'])

    def test_muda_papel_e_registra(self):
        response = self.post('user_admin_role', self.ana, {'role': 'ORGANIZER'})
        self.assertRedirects(response, self.detail(self.ana))
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.role, User.Role.ORGANIZER)
        change = UserChange.objects.get()
        self.assertEqual((change.action, change.actor, change.target), ('ROLE', self.admin, self.ana))
        self.assertEqual(change.details, {'antes': 'Participante', 'depois': 'Organizador'})
        self.assertContains(self.client.get(self.detail(self.ana)), 'Papel alterado')

    def test_nao_altera_a_propria_conta(self):
        response = self.post('user_admin_role', self.admin, {'role': 'PARTICIPANT'})
        self.post('user_admin_active', self.admin, {'active': '0'})
        self.admin.refresh_from_db()
        self.assertEqual((self.admin.role, self.admin.is_active), (User.Role.ADMIN, True))
        self.assertContains(self.client.get(response.url), 'não pode alterar a própria conta')
        self.assertFalse(UserChange.objects.exists())

    def test_outro_admin_pode_ser_rebaixado(self):
        other = make_user('outro', User.Role.ADMIN)
        self.post('user_admin_role', other, {'role': 'ORGANIZER'})
        other.refresh_from_db()
        self.assertEqual(other.role, User.Role.ORGANIZER)

    def test_regra_do_ultimo_admin(self):
        # Pelo painel quem age é sempre um administrador ativo, então sempre sobra um; a regra protege as
        # chamadas diretas (comandos, scripts) de deixar o portal sem nenhum.
        from . import user_admin

        organizer = make_user('org', User.Role.ORGANIZER)
        with self.assertRaisesMessage(user_admin.Refused, 'última conta de administrador'):
            user_admin.change_role(organizer, self.admin, User.Role.PARTICIPANT)
        with self.assertRaisesMessage(user_admin.Refused, 'última conta de administrador'):
            user_admin.set_active(organizer, self.admin, False)
        with self.assertRaisesMessage(user_admin.Refused, 'última conta de administrador'):
            user_admin.delete_account(organizer, self.admin)
        make_user('outro', User.Role.ADMIN)
        self.assertTrue(user_admin.change_role(organizer, self.admin, User.Role.PARTICIPANT))

    def test_superusuario_so_por_superusuario(self):
        root = make_user('root', User.Role.PARTICIPANT, is_superuser=True, is_staff=True)
        response = self.post('user_admin_active', root, {'active': '0'})
        root.refresh_from_db()
        self.assertTrue(root.is_active)
        self.assertContains(self.client.get(response.url), 'Só um superusuário')

    def test_desativar_encerra_sessoes_e_reativar(self):
        other = Client()
        other.force_login(self.ana)
        self.assertEqual(Session.objects.count(), 2)
        self.post('user_admin_active', self.ana, {'active': '0'})
        self.ana.refresh_from_db()
        self.assertFalse(self.ana.is_active)
        self.assertEqual(Session.objects.count(), 1)  # só a do administrador
        self.assertEqual(UserChange.objects.get().details, {'sessoes_encerradas': 1})
        self.assertFalse(self.client.login(username='ana', password=PASSWORD))
        self.post('user_admin_active', self.ana, {'active': '1'})
        self.ana.refresh_from_db()
        self.assertTrue(self.ana.is_active)

    def test_duas_etapas_link_de_senha_e_codigo(self):
        MfaDevice.objects.create(user=self.ana, secret='x', confirmed_at='2026-01-01T00:00Z')
        self.post('user_admin_mfa_reset', self.ana)
        self.assertFalse(MfaDevice.objects.filter(user=self.ana).exists())
        mail.outbox.clear()
        self.post('user_admin_password_link', self.ana)
        self.assertEqual(mail.outbox[-1].to, ['ana@exemplo.com'])
        self.assertIn('/accounts/reset/', mail.outbox[-1].body)
        self.ana.refresh_from_db()
        self.assertTrue(self.ana.check_password(PASSWORD))  # o administrador não mexe na senha
        response = self.post('user_admin_resend_code', self.ana)  # e-mail já confirmado
        self.assertContains(self.client.get(response.url), 'já está confirmado')
        User.objects.filter(pk=self.ana.pk).update(email_verified=False)
        self.post('user_admin_resend_code', self.ana)
        self.assertTrue(EmailCode.objects.filter(user=self.ana, used_at__isnull=True).exists())
        actions = list(UserChange.objects.order_by('id').values_list('action', flat=True))
        self.assertEqual(actions, ['MFA_RESET', 'PASSWORD_LINK', 'CODE_RESENT'])

    def test_excluir_anonimiza(self):
        organizer = make_user('org', User.Role.ORGANIZER, first_name='Otto', last_name='Org')
        self.post('user_admin_delete', organizer, {'confirm': 'apagar'})
        organizer.refresh_from_db()
        self.assertTrue(organizer.is_active)
        self.post('user_admin_delete', organizer, {'confirm': 'excluir'})
        organizer.refresh_from_db()
        self.assertEqual((organizer.is_active, organizer.role, organizer.first_name), (False, 'PARTICIPANT', 'Conta'))
        change = UserChange.objects.get(action='ANONYMIZED')
        self.assertEqual(change.details, {'papel': 'Organizador', 'inscricoes_canceladas': 0})
        # nada pessoal da conta excluída sobra no histórico (SEC-26 da 3ª auditoria)
        for row in UserChange.objects.filter(target=organizer).values('target_name', 'details'):
            self.assertNotIn('Otto', str(row))
            self.assertNotIn('org@exemplo.com', str(row))
        page = self.client.get(self.detail(organizer))
        self.assertContains(page, 'Esta conta foi excluída')
        self.post('user_admin_active', organizer, {'active': '1'})
        organizer.refresh_from_db()
        self.assertFalse(organizer.is_active)

    def test_pede_a_senha_de_novo(self):
        expire_reauth(self.client)
        response = self.post('user_admin_role', self.ana, {'role': 'ADMIN'})
        self.assertTrue(response.url.startswith(reverse('reauth')))
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.role, User.Role.PARTICIPANT)


class DjangoAdminUserTests(TestCase):
    def test_sem_grupos_papel_so_leitura_e_sem_apagar(self):
        root = make_user('root', User.Role.ADMIN, is_superuser=True, is_staff=True)
        ana = make_user('ana')
        self.client.force_login(root)
        page = self.client.get(reverse('admin:accounts_user_change', args=[ana.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'name="groups"')
        self.assertNotContains(page, 'name="user_permissions"')
        self.assertNotContains(page, 'name="role"')
        self.assertEqual(self.client.get(reverse('admin:accounts_user_delete', args=[ana.pk])).status_code, 403)


class DeletedAccountNameTests(TestCase):
    """SEC-21 da 3ª auditoria: o nome da conta excluída não pode ser reservado por outra pessoa."""

    def test_cadastro_recusa_o_prefixo_reservado(self):
        data = {'first_name': 'X', 'last_name': 'Y', 'email': 'x@exemplo.com', 'institutional_id': '',
                'password1': 'Senha-forte-123', 'password2': 'Senha-forte-123', 'accept_privacy': 'on'}  # fmt: skip
        for name in ('excluido-57', 'Excluido57', 'EXCLUIDO'):
            response = self.client.post(reverse('signup'), {**data, 'username': name})
            self.assertContains(response, 'reservado pelo sistema')
        self.assertFalse(User.objects.filter(username__istartswith='excluido').exists())

    def test_exclusao_funciona_mesmo_com_nome_antigo_ocupado(self):
        from .privacy import anonymize

        victim = make_user('vitima')
        User.objects.create_user(f'excluido-{victim.pk}', 'z@exemplo.com', PASSWORD)  # conta antiga com o nome
        anonymize(victim)
        victim.refresh_from_db()
        self.assertTrue(victim.is_deleted)
        self.assertTrue(victim.username.startswith(f'excluido-{victim.pk}-'))
        other = make_user('outra')
        anonymize(other)
        other.refresh_from_db()
        self.assertNotEqual(other.username.rsplit('-', 1)[1], victim.username.rsplit('-', 1)[1])


class UserChangeAppendOnlyTests(TestCase):
    """BD-07 da 3ª auditoria: o histórico de contas não pode ser alterado nem apagado, nem pelo banco."""

    def test_banco_recusa_alterar_e_apagar(self):
        from django.db import DatabaseError, transaction

        from . import user_admin

        admin = make_user('chefe', User.Role.ADMIN)
        user_admin.change_role(admin, make_user('ana'), User.Role.ORGANIZER)
        change = UserChange.objects.get()
        with self.assertRaises(DatabaseError), transaction.atomic():
            UserChange.objects.filter(pk=change.pk).update(details={})
        with self.assertRaises(DatabaseError), transaction.atomic():
            UserChange.objects.filter(pk=change.pk).delete()

    def test_apagar_conta_nao_mexe_no_historico(self):
        # a faxina apaga cadastros não confirmados; se um administrador tinha reenviado o código, o histórico fica
        from django.test import RequestFactory

        from . import user_admin

        admin = make_user('chefe', User.Role.ADMIN)
        pending = make_user('novo', email_verified=False)
        request = RequestFactory().post('/')
        request.user = admin
        request.session = {}
        user_admin.resend_code(admin, pending, request)
        pending_pk = pending.pk
        pending.delete()
        change = UserChange.objects.get()
        self.assertEqual((change.target_id, change.target_name), (pending_pk, f'conta {pending_pk}'))


class SessionTrackingTests(TestCase):
    """DES-05 da 3ª auditoria: encerrar as sessões de uma pessoa sem decodificar as de todo mundo."""

    def test_registra_e_encerra_so_as_da_pessoa(self):
        from unittest import mock

        from django.contrib.sessions.backends.db import SessionStore

        from .models import UserSession
        from .sessions import end_sessions

        ana, bia = make_user('ana'), make_user('bia')
        phone, laptop, other = Client(), Client(), Client()
        for client, user in ((phone, ana), (laptop, ana), (other, bia)):
            client.force_login(user)
            client.get(reverse('profile'))  # a primeira requisição registra a sessão
        self.assertEqual(UserSession.objects.filter(user=ana).count(), 2)
        with mock.patch.object(SessionStore, 'decode', side_effect=AssertionError('não deveria decodificar')):
            self.assertEqual(end_sessions(ana), 2)  # todas registradas: nenhuma sessão é aberta
        self.assertEqual(Session.objects.count(), 1)
        self.assertFalse(UserSession.objects.filter(user=ana).exists())

    def test_troca_de_chave_no_login_registra_a_nova(self):
        from .models import UserSession

        make_user('ana')
        self.client.post(reverse('login'), {'username': 'ana', 'password': PASSWORD})
        key = self.client.session.session_key
        self.client.get(reverse('profile'))
        self.assertTrue(UserSession.objects.filter(session_key=key).exists())


class TransferAndCsvTests(TestCase):
    """P3 da 3ª auditoria: transferência de atividades e emissões, e a lista de usuários em CSV."""

    def setUp(self):
        from datetime import date

        from activities.test_utils import make_activity
        from certificates.models import CertificateBatch

        self.admin = make_user('chefe', User.Role.ADMIN)
        self.old = make_user('saiu', User.Role.ORGANIZER, first_name='Otto', last_name='Antigo')
        self.new = make_user('entrou', User.Role.ORGANIZER, first_name='Nina', last_name='Nova')
        self.activity = make_activity(self.old)
        self.batch = CertificateBatch.objects.create(
            title='Semana', start_date=date(2026, 1, 1), end_date=date(2026, 1, 1), issue_date=date(2026, 1, 1),
            created_by=self.old,
        )  # fmt: skip
        self.client.force_login(self.admin)

    def test_transfere_e_mantem_a_assinatura(self):
        from certificates.batch import activity_signer, signer_for

        url = reverse('user_admin_transfer', args=[self.old.pk])
        self.assertRedirects(
            self.client.post(url, {'to': self.new.pk}), reverse('user_admin_detail', args=[self.old.pk])
        )
        self.activity.refresh_from_db()
        self.batch.refresh_from_db()
        self.assertEqual((self.activity.organizer, self.batch.created_by), (self.new, self.new))
        self.assertEqual(activity_signer(self.activity), 'Otto Antigo')  # certificados já emitidos não mudam
        self.assertEqual(signer_for(self.batch), 'Otto Antigo')
        change = UserChange.objects.get(action='TRANSFERRED')
        self.assertEqual(change.details, {'atividades': 1, 'emissoes_avulsas': 1, 'para': f'conta {self.new.pk}'})

    def test_so_recebe_quem_e_da_equipe(self):
        participant = make_user('ana')
        self.client.post(reverse('user_admin_transfer', args=[self.old.pk]), {'to': participant.pk})
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.organizer, self.old)

    def test_excluir_organizador_mantem_a_assinatura(self):
        from certificates.batch import activity_signer

        self.client.post(reverse('user_admin_delete', args=[self.old.pk]), {'confirm': 'EXCLUIR'})
        self.activity.refresh_from_db()
        self.assertEqual(activity_signer(self.activity), 'Otto Antigo')
        # e as atividades da conta excluída ainda podem ir para outra pessoa
        self.client.post(reverse('user_admin_transfer', args=[self.old.pk]), {'to': self.new.pk})
        self.activity.refresh_from_db()
        self.assertEqual((self.activity.organizer, activity_signer(self.activity)), (self.new, 'Otto Antigo'))

    def test_csv_com_filtros_pede_a_senha(self):
        response = self.client.get(reverse('user_admin_csv'), {'papel': 'ORGANIZER'})
        content = response.content.decode('utf-8-sig')
        self.assertIn('Usuário;Nome;E-mail', content)
        self.assertIn('saiu;Otto Antigo', content)
        self.assertNotIn('chefe', content)
        expire_reauth(self.client)
        self.assertTrue(self.client.get(reverse('user_admin_csv')).url.startswith(reverse('reauth')))
        self.client.force_login(make_user('org2', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(reverse('user_admin_csv')).status_code, 403)
