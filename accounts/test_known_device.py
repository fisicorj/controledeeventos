"""APP-08: o teto de senhas erradas por usuário não trava o dono no navegador em que ele já entrou."""

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts import known_device
from accounts.forms import LOGIN_FAILS_PER_USER
from activities.test_utils import PASSWORD, make_user


@override_settings(NUM_PROXIES=1)
class KnownDeviceTests(TestCase):
    def setUp(self):
        self.user = make_user('ana')
        self.url = reverse('login')

    def attack(self):
        """Erros de muitos IPs diferentes (4 por IP, abaixo do limite por usuário e IP), até o teto do usuário."""
        attacker = Client()
        for i in range(LOGIN_FAILS_PER_USER):
            attacker.post(
                self.url,
                {'username': 'ana', 'password': 'errada'},
                HTTP_X_FORWARDED_FOR=f'198.51.100.{i // 4 + 1}',
            )

    def logout(self, client):
        """Sai sem perder o cookie do navegador (o logout do Client de teste apaga todos os cookies)."""
        cookie = client.cookies.get(known_device.COOKIE)
        client.logout()
        if cookie is not None:
            client.cookies[known_device.COOKIE] = cookie.value

    def login(self, client, ip='203.0.113.5'):
        return client.post(self.url, {'username': 'ana', 'password': PASSWORD}, HTTP_X_FORWARDED_FOR=ip)

    def test_navegador_conhecido_continua_entrando(self):
        owner = Client()
        response = self.login(owner)
        self.assertEqual(response.status_code, 302)
        self.assertIn(known_device.COOKIE, response.cookies)
        self.logout(owner)
        self.attack()
        response = self.login(owner, ip='203.0.113.77')  # outro IP (celular, casa), mesmo navegador
        self.assertEqual(response.status_code, 302)

    def test_navegador_novo_fica_bloqueado(self):
        self.attack()
        response = self.login(Client())
        self.assertContains(response, 'Muitas tentativas de entrar')

    def test_limite_por_usuario_e_ip_vale_no_navegador_conhecido(self):
        owner = Client()
        self.login(owner)
        self.logout(owner)
        for _ in range(5):
            owner.post(self.url, {'username': 'ana', 'password': 'errada'}, HTTP_X_FORWARDED_FOR='203.0.113.5')
        self.assertContains(self.login(owner), 'Muitas tentativas de entrar')

    def test_cookie_forjado_ou_de_outro_usuario_nao_vale(self):
        make_user('bia')
        owner = Client()
        owner.post(self.url, {'username': 'bia', 'password': PASSWORD})
        self.logout(owner)
        self.attack()
        self.assertContains(self.login(owner), 'Muitas tentativas de entrar')  # cookie é da bia
        forged = Client()
        forged.cookies[known_device.COOKIE] = 'abc:def'
        self.assertContains(self.login(forged), 'Muitas tentativas de entrar')
