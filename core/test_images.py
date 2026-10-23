"""S-01/S-07 da 8ª auditoria: o sistema só abre imagens JPEG, PNG e WebP."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from PIL import Image

from accounts.models import User
from activities.test_utils import make_activity, make_user
from core.images import ALLOWED_FORMATS, INVALID_IMAGE


def image_bytes(fmt):
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), (200, 30, 30)).save(buffer, fmt)
    return buffer.getvalue()


class PillowRestrictionTests(SimpleTestCase):
    def test_so_os_formatos_permitidos_abrem(self):
        self.assertEqual(sorted(Image.OPEN), sorted(ALLOWED_FORMATS))
        for fmt in ('JPEG', 'PNG', 'WEBP'):
            with self.subTest(fmt):
                Image.open(io.BytesIO(image_bytes(fmt))).load()
        for fmt in ('GIF', 'BMP', 'TIFF'):
            with self.subTest(fmt), self.assertRaises(Image.UnidentifiedImageError):
                Image.open(io.BytesIO(image_bytes(fmt)))


class SpeakerPhotoTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.client.force_login(self.org)

    def post(self, name, content):
        return self.client.post(
            reverse('speaker_create', args=[self.activity.pk]),
            {'name': 'Paula', 'order': 1, 'photo': SimpleUploadedFile(name, content)},
        )

    def test_foto_em_formato_aceito_entra_e_outro_e_recusado_com_mensagem_clara(self):
        response = self.post('foto.gif', image_bytes('GIF'))
        self.assertContains(response, INVALID_IMAGE)
        self.assertContains(response, 'accept="image/jpeg,image/png,image/webp"')
        self.assertFalse(self.activity.speakers.exists())
        self.post('foto.png', image_bytes('PNG'))
        self.assertTrue(self.activity.speakers.exists())
