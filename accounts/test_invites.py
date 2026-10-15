"""Convite para a equipe (accounts/invites.py)."""

import re
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from activities.test_utils import make_user
from core.models import OutgoingEmail

from .models import StaffInvite, User, UserChange

FORM = {
    'username': 'nina', 'first_name': 'Nina', 'last_name': 'Nova', 'password1': 'Senha-forte-123',
    'password2': 'Senha-forte-123', 'accept_privacy': 'on',
}  # fmt: skip


@override_settings(OUTBOX_MODE='off')
class InviteTests(TestCase):
    def setUp(self):
        self.admin = make_user('chefe', User.Role.ADMIN)
        self.client.force_login(self.admin)

    def invite(self, email='nina@exemplo.com', role='ORGANIZER'):
        response = self.client.post(reverse('user_admin_invites'), {'email': email, 'role': role})
        body = OutgoingEmail.objects.filter(to=email, group='invite').order_by('-id').first()
        return response, body

    def link(self, mail):
        """Caminho do link do convite que saiu no e-mail."""
        return re.search(r'/accounts/convite/[^/\s]+/', mail.body).group(0)

    def test_convite_cria_conta_com_papel_e_email_confirmado(self):
        response, mail = self.invite()
        self.assertRedirects(response, reverse('user_admin_invites'))
        path = self.link(mail)
        visitor = Client()
        page = visitor.get(path)
        self.assertContains(page, 'nina@exemplo.com')
        response = visitor.post(path, FORM)
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        nina = User.objects.get(username='nina')
        self.assertEqual((nina.role, nina.email, nina.email_verified), ('ORGANIZER', 'nina@exemplo.com', True))
        self.assertIsNotNone(nina.privacy_accepted_at)
        self.assertEqual(StaffInvite.objects.get().accepted_user, nina)
        accepted = UserChange.objects.get(action='INVITE_ACCEPTED')
        self.assertEqual((accepted.actor, accepted.target), (self.admin, nina))
        # o link não vale de novo
        self.assertEqual(Client().get(path).status_code, 404)

    def test_no_banco_so_o_hash_do_token(self):
        _, mail = self.invite()
        token = self.link(mail).strip('/').rsplit('/', 1)[-1]
        self.assertFalse(StaffInvite.objects.filter(token_hash=token).exists())

    def test_email_com_conta_confirmada_nao_recebe_convite(self):
        make_user('ana', email_verified=True)
        response, mail = self.invite('ana@exemplo.com')
        self.assertContains(response, 'já tem conta confirmada')
        self.assertIsNone(mail)

    def test_cancelado_e_vencido_nao_valem(self):
        _, mail = self.invite()
        path = self.link(mail)
        invite = StaffInvite.objects.get()
        self.client.post(reverse('user_admin_invite_cancel', args=[invite.pk]))
        self.assertEqual(Client().get(path).status_code, 404)
        _, mail = self.invite()  # novo convite para o mesmo e-mail
        path = self.link(mail)
        StaffInvite.objects.filter(cancelled_at__isnull=True).update(expires_at=timezone.now() - timedelta(minutes=1))
        response = Client().post(path, FORM)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(User.objects.filter(username='nina').exists())

    def test_so_administrador_convida(self):
        self.client.force_login(make_user('org', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(reverse('user_admin_invites')).status_code, 403)
        response, _ = self.invite()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(StaffInvite.objects.exists())

    def test_nome_reservado_e_papel_de_participante(self):
        response, _ = self.invite(role='PARTICIPANT')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StaffInvite.objects.exists())
        _, mail = self.invite()
        path = self.link(mail)
        page = Client().post(path, {**FORM, 'username': 'excluido-9'})
        self.assertContains(page, 'reservado pelo sistema')

    def test_historico_liga_ao_convite_e_faxina_apaga_os_antigos(self):
        # SEC-27 da 4ª auditoria
        from core import maintenance

        self.invite('velho@exemplo.com')
        self.invite('cancelado@exemplo.com')
        _, mail = self.invite('aceito@exemplo.com')
        Client().post(self.link(mail), {**FORM, 'username': 'aceito'})
        invite = StaffInvite.objects.get(email='velho@exemplo.com')
        sent = UserChange.objects.filter(action='INVITED').values_list('details', flat=True)
        self.assertIn({'convite': invite.pk, 'papel': 'Organizador'}, list(sent))
        self.assertNotIn('velho@exemplo.com', str(list(sent)))
        long_ago = timezone.now() - timedelta(days=40)
        StaffInvite.objects.filter(email='velho@exemplo.com').update(expires_at=long_ago)
        StaffInvite.objects.filter(email='cancelado@exemplo.com').update(cancelled_at=long_ago, expires_at=long_ago)
        StaffInvite.objects.filter(email='aceito@exemplo.com').update(expires_at=long_ago)
        self.assertEqual(maintenance.purge_invites(), 2)
        self.assertEqual(list(StaffInvite.objects.values_list('email', flat=True)), ['aceito@exemplo.com'])
