import datetime
import io
import shutil
import tempfile

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from accounts.models import User
from activities.test_utils import make_activity, make_session, make_user
from attendance.models import Attendance
from core.models import SiteSettings
from registrations.models import Registration

from .models import Certificate
from .pdf import certificate_pdf, certificate_text, long_date, period_phrase
from .services import issue_certificates

TZ = timezone.get_default_timezone()


def dt(*args):
    return datetime.datetime(*args, tzinfo=TZ)


class PeriodTextTests(SimpleTestCase):
    def test_periodos(self):
        self.assertEqual(period_phrase(dt(2026, 8, 29, 8), dt(2026, 8, 29, 12)), 'no dia 29 de agosto de 2026')
        self.assertEqual(period_phrase(dt(2026, 8, 10, 8), dt(2026, 8, 14, 12)), 'de 10 a 14 de agosto de 2026')
        self.assertEqual(
            period_phrase(dt(2026, 8, 28, 8), dt(2026, 9, 2, 12)), 'de 28 de agosto a 2 de setembro de 2026'
        )
        self.assertEqual(
            period_phrase(dt(2026, 12, 30, 8), dt(2027, 1, 2, 12)), 'de 30 de dezembro de 2026 a 2 de janeiro de 2027'
        )
        self.assertEqual(long_date(dt(2026, 9, 1, 10)), '1º de setembro de 2026')


class CertificateTextTests(TestCase):
    def certificate(self, **kw):
        data = {
            'code': 'ABCD-EFGH-JKMN', 'participant_name': 'Giovanna Banqueri Somensari',
            'participant_document': '51220011', 'activity_title': 'Rally do Conhecimento', 'activity_type': 'Evento',
            'start_date': dt(2026, 8, 29, 8), 'end_date': dt(2026, 8, 29, 12), 'workload_hours': 4,
            'attendance_percent': 100, 'issued_at': dt(2026, 9, 22, 10),
        }  # fmt: skip
        data.update(kw)
        return Certificate(**data)

    def flat(self, body):
        return [''.join(text for text, _ in line) for line in body]

    def test_texto_igual_ao_modelo(self):
        site = SiteSettings.load()
        site.certificate_opening = 'Declaramos que'
        site.certificate_place = 'na Faculdade Engenheiro Salvador Arena'
        site.city = 'São Bernardo do Campo'
        site.certificate_title = ''
        texts = certificate_text(self.certificate(), site)
        self.assertEqual(texts['opening'], 'Declaramos que')
        self.assertEqual(texts['name'], 'Giovanna Banqueri Somensari,')
        self.assertEqual(
            self.flat(texts['body']),
            [
                'participou do “Rally do Conhecimento”,',
                'realizado na Faculdade Engenheiro Salvador Arena,',
                'no dia 29 de agosto de 2026, com duração de 4 horas.',
            ],
        )
        self.assertTrue(texts['body'][0][1][1])  # título em negrito
        self.assertEqual(texts['date_line'], 'São Bernardo do Campo, 22 de setembro de 2026.')
        self.assertEqual(texts['title'], '')

    def test_sem_local_e_sem_carga_horaria(self):
        site = SiteSettings.load()
        site.certificate_place = ''
        site.city = ''
        texts = certificate_text(self.certificate(workload_hours=None), site)
        self.assertEqual(self.flat(texts['body'])[1], 'realizado no dia 29 de agosto de 2026.')
        self.assertEqual(texts['date_line'], '22 de setembro de 2026.')
        texts = certificate_text(self.certificate(workload_hours=1), site)
        self.assertIn('com duração de 1 hora.', self.flat(texts['body'])[1])


MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class BackgroundTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_matricula_guardada_e_pdf_com_arte(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org, title='Rally do Conhecimento' + ' muito longo' * 12)
        session = make_session(activity)
        user = make_user('ana', first_name='Ana', last_name='Souza', institutional_id='51220011')
        registration = Registration.objects.create(user=user, activity=activity)
        Attendance.objects.create(registration=registration, session=session, validated_by=org)
        certificate = issue_certificates(activity)[0]
        self.assertEqual(certificate.participant_document, '51220011')

        site = SiteSettings.load()
        buffer = io.BytesIO()
        Image.new('RGB', (1188, 840), (11, 79, 138)).save(buffer, format='JPEG')
        site.certificate_background.save('fundo.jpg', ContentFile(buffer.getvalue()), save=False)
        site.certificate_title = ''
        site.certificate_show_qr = False
        site.save()
        pdf = certificate_pdf(certificate, 'Org')
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertIn(b'/Subtype /Image', pdf)
        self.assertIn(b'Carlito', pdf)  # fonte embutida
