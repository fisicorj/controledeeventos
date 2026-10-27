"""Chave própria dos segredos guardados no banco e troca de chaves sem perder nada (core/crypto.py)."""

from django.test import TestCase, override_settings

from accounts.models import MfaDevice, User

from . import crypto
from .models import EmailSettings

OLD_SECRET = 'secret-key-antiga-0123456789-abcdefghijklmnopqrstuvwxyz-0123'
NEW_SECRET = 'secret-key-nova-9876543210-zyxwvutsrqponmlkjihgfedcba-9876543'
FIELD_KEY = 'chave-propria-dos-segredos-0123456789-abcdef'
FIELD_KEY_2 = 'outra-chave-propria-dos-segredos-9876543210'


class CryptoTests(TestCase):
    def test_sem_chave_propria_usa_a_secret_key(self):
        with override_settings(SECRET_KEY=OLD_SECRET, FIELD_ENCRYPTION_KEY=''):
            token = crypto.encrypt('senha')
        with override_settings(SECRET_KEY=OLD_SECRET, FIELD_ENCRYPTION_KEY=''):
            self.assertEqual(crypto.decrypt(token), 'senha')
        with override_settings(SECRET_KEY=NEW_SECRET, FIELD_ENCRYPTION_KEY=''):
            with self.assertRaises(crypto.SecretUnreadable):
                crypto.decrypt(token)

    def test_chave_propria_independe_da_secret_key(self):
        with override_settings(SECRET_KEY=OLD_SECRET, FIELD_ENCRYPTION_KEY=FIELD_KEY):
            token = crypto.encrypt('senha')
        # vazou e trocou a SECRET_KEY: o segredo continua legível só com a chave própria
        with override_settings(SECRET_KEY=NEW_SECRET, FIELD_ENCRYPTION_KEY=FIELD_KEY):
            self.assertEqual(crypto.decrypt(token), 'senha')
        # e só a SECRET_KEY não basta para ler
        with override_settings(SECRET_KEY=OLD_SECRET, FIELD_ENCRYPTION_KEY=''):
            with self.assertRaises(crypto.SecretUnreadable):
                crypto.decrypt(token)

    def test_troca_da_secret_key_com_fallback(self):
        with override_settings(SECRET_KEY=OLD_SECRET, FIELD_ENCRYPTION_KEY=''):
            token = crypto.encrypt('senha')
        with override_settings(SECRET_KEY=NEW_SECRET, SECRET_KEY_FALLBACKS=[OLD_SECRET], FIELD_ENCRYPTION_KEY=''):
            self.assertEqual(crypto.decrypt(token), 'senha')
            self.assertTrue(crypto.needs_reencrypt(token))
            fresh = crypto.reencrypt(token)
            self.assertFalse(crypto.needs_reencrypt(fresh))
        with override_settings(SECRET_KEY=NEW_SECRET, SECRET_KEY_FALLBACKS=[], FIELD_ENCRYPTION_KEY=''):
            self.assertEqual(crypto.decrypt(fresh), 'senha')

    def test_troca_da_chave_propria_com_fallback(self):
        with override_settings(FIELD_ENCRYPTION_KEY=FIELD_KEY):
            token = crypto.encrypt('senha')
        with override_settings(FIELD_ENCRYPTION_KEY=FIELD_KEY_2, FIELD_ENCRYPTION_KEY_FALLBACKS=[FIELD_KEY]):
            self.assertEqual(crypto.decrypt(token), 'senha')
            self.assertTrue(crypto.needs_reencrypt(token))

    def test_faxina_regrava_com_a_chave_nova(self):
        """Definir a FIELD_ENCRYPTION_KEY numa instalação antiga: nada se perde e a faxina migra tudo."""
        user = User.objects.create_user('admin', 'admin@exemplo.com', 'x')
        with override_settings(SECRET_KEY=OLD_SECRET, FIELD_ENCRYPTION_KEY=''):
            email = EmailSettings.load(fresh=True)
            email.set_password('smtp')
            email.save()
            MfaDevice.objects.create(user=user, secret=crypto.encrypt('TOTPSECRET'))
            MfaDevice.objects.create(user=User.objects.create_user('b', 'b@exemplo.com', 'x'), secret='lixo')
        with override_settings(SECRET_KEY=OLD_SECRET, FIELD_ENCRYPTION_KEY=FIELD_KEY):
            self.assertEqual(crypto.reencrypt_all(), 2)  # o ilegível fica como está
            self.assertEqual(crypto.reencrypt_all(), 0)
        # já regravados: a SECRET_KEY antiga pode sair
        with override_settings(SECRET_KEY=NEW_SECRET, FIELD_ENCRYPTION_KEY=FIELD_KEY):
            self.assertEqual(EmailSettings.load(fresh=True).get_password(), 'smtp')
            self.assertEqual(crypto.decrypt(MfaDevice.objects.get(user=user).secret), 'TOTPSECRET')
