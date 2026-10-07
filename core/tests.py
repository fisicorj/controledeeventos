import io
import shutil
import tempfile

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import User
from activities.test_utils import make_activity, make_user
from certificates.models import Certificate
from certificates.pdf import certificate_pdf
from registrations.models import Registration

from .models import SiteSettings

MEDIA = tempfile.mkdtemp()


def png(name, size=(300, 120)):
    buffer = io.BytesIO()
    Image.new('RGBA', size, (11, 79, 138, 255)).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA)
class SiteSettingsTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_registro_unico_com_valores_padrao(self):
        first = SiteSettings.load()
        self.assertEqual(first.institution_name, 'Arena+')
        SiteSettings(institution_name='Outra').save()  # sempre grava no mesmo registro
        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertEqual(SiteSettings.load().institution_name, 'Outra')

    def test_so_administrador_acessa(self):
        url = reverse('site_settings')
        self.client.force_login(make_user('org', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertNotContains(self.client.get('/'), url)
        self.assertEqual(self.client.get(reverse('certificate_preview')).status_code, 403)
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        self.assertContains(self.client.get('/'), url)  # link no menu
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_salvar_e_usar_nos_emails_e_pdfs(self):
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        response = self.client.post(
            reverse('site_settings'),
            {
                'portal_name': 'Eventos Fatec',
                'primary_color': '#7A1F2B',
                'institution_name': 'Fatec Ipiranga',
                'city': 'São Paulo',
                'signer_name': 'Profa. Maria Souza',
                'signer_role': 'Diretora Acadêmica',
                'logo': png('logo.png'),
                'signature_image': png('assinatura.png', (400, 100)),
                'certificate_background': png('fundo.png', (1188, 840)),
                'certificate_title': '',
                'certificate_opening': 'Declaramos que',
                'certificate_place': 'na Fatec Ipiranga',
                'document_label': 'RA',
                'certificate_offset_mm': 16,
                'certificate_show_qr': 'on',
            },
            follow=True,
        )
        self.assertContains(response, 'Configurações salvas.')
        site = SiteSettings.load()
        self.assertEqual(site.institution_name, 'Fatec Ipiranga')
        # identidade aplicada no site: cor, nome do portal e logo no topo
        home = self.client.get('/')
        self.assertContains(home, '--brand: #7A1F2B;')
        self.assertContains(home, '--brand-contrast: #ffffff')
        self.assertContains(home, 'Eventos Fatec')
        self.assertContains(home, site.logo.url)
        # telas de login/senha do Django também recebem a identidade (lá existe outra variável "site")
        self.client.logout()
        for url in (reverse('login'), reverse('password_reset')):
            page = self.client.get(url)
            self.assertContains(page, '--brand: #7A1F2B;')
            self.assertContains(page, site.logo.url)
        self.assertContains(self.client.get(reverse('login')), 'senha do Eventos Fatec')
        self.client.force_login(make_user('adm2', User.Role.ADMIN))
        self.assertTrue(site.logo.name.startswith('institution/'))
        self.assertTrue(site.certificate_background.name.startswith('institution/'))
        self.assertEqual((site.document_label, site.certificate_offset_mm), ('RA', 16))

        # pré-visualização do certificado com logo e assinatura
        preview = self.client.get(reverse('certificate_preview'))
        self.assertEqual(preview['Content-Type'], 'application/pdf')
        self.assertTrue(preview.content.startswith(b'%PDF'))
        self.assertIn(b'/Subtype /Image', preview.content)

        # e-mails passam a usar o nome configurado
        activity = make_activity(make_user('org', User.Role.ORGANIZER))
        self.client.force_login(make_user('ana'))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('register_activity', args=[activity.id]))
        self.assertTrue(mail.outbox[0].subject.startswith('Fatec Ipiranga:'))
        self.assertIn('Fatec Ipiranga', mail.outbox[0].body)

    def test_cor_invalida_e_contraste(self):
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        response = self.client.post(
            reverse('site_settings'), {'portal_name': 'X', 'primary_color': 'azul', 'institution_name': 'X'}
        )
        self.assertContains(response, 'Use uma cor no formato #RRGGBB.')
        site = SiteSettings.load()
        site.primary_color = '#FFE59A'  # cor clara: texto escuro nos botões
        self.assertEqual(site.primary_contrast, '#1c2533')
        self.assertEqual(site.primary_rgb, '255, 229, 154')

    def test_imagem_invalida_e_recusada(self):
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        fake = SimpleUploadedFile('logo.png', b'isto nao e uma imagem', content_type='image/png')
        response = self.client.post(
            reverse('site_settings'),
            {'portal_name': 'X', 'primary_color': '#123456', 'institution_name': 'X', 'logo': fake},
        )
        self.assertContains(response, 'Envie uma imagem válida')

    def test_imagem_apagada_do_disco_nao_quebra_o_pdf(self):
        site = SiteSettings.load()
        site.logo = 'institution/sumiu.png'
        site.save()
        org = make_user('org', User.Role.ORGANIZER)
        registration = Registration.objects.create(user=make_user('ana'), activity=make_activity(org))
        certificate = Certificate.objects.create(
            registration=registration, participant_name='Ana', activity_title='Curso', activity_type='Curso',
            start_date=registration.activity.start_date, end_date=registration.activity.end_date,
            attendance_percent=100,
        )  # fmt: skip
        self.assertTrue(certificate_pdf(certificate, 'Org').startswith(b'%PDF'))
