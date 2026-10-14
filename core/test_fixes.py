"""Correções da revisão geral: segurança, check-in, e-mails, perfil, páginas de erro, status e CSV."""

import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.mail import EmailMessage
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activities.models import Activity, Material
from activities.test_utils import PASSWORD, make_activity, make_session, make_user, now
from attendance.models import Attendance
from certificates.models import Certificate
from core.csvsafe import safe_cell
from core.models import SiteSettings
from registrations import notifications
from registrations.models import Registration, SurveyResponse

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def png():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'navy').save(buffer, 'PNG')
    return buffer.getvalue()


class PrivateFilesMixin:
    def use_private_root(self):
        self.private = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.private, ignore_errors=True)
        override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.private))
        override.enable()
        self.addCleanup(override.disable)


class CheckinAppSafetyTests(TestCase):
    """A1 e A3 no navegador: a lista de recusas não vira HTML e a fila não se perde."""

    def test_lista_de_recusas_em_texto_puro_e_fila_preservada(self):
        org = make_user('org', User.Role.ORGANIZER)
        session = make_session(make_activity(org), check_in_open=True)
        self.client.force_login(org)
        self.assertContains(self.client.get(reverse('checkin_app', args=[session.id])), 'js/checkin-app.js')
        page = (settings.BASE_DIR / 'static' / 'js' / 'checkin-app.js').read_text(encoding='utf-8')
        self.assertNotIn('`<li>${p}</li>`', page)
        self.assertIn('li.textContent = p', page)
        self.assertIn("redirect: 'manual'", page)  # login expirado não esvazia a fila
        self.assertIn('r.status >= 500', page)  # servidor instável mantém as leituras


class MaterialSafetyTests(PrivateFilesMixin, TestCase):
    """A2: material não pode ser página HTML/SVG, e o que não é PDF/imagem é baixado."""

    def setUp(self):
        self.use_private_root()
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.client.force_login(self.org)

    def upload(self, name, content=b'conteudo'):
        return self.client.post(
            reverse('material_create', args=[self.activity.id]),
            {'title': name, 'audience': 'CONFIRMED', 'order': 1, 'url': '',
             'file': SimpleUploadedFile(name, content)},
        )  # fmt: skip

    def test_html_e_svg_recusados(self):
        for name in ('ataque.html', 'imagem.svg', 'script.js'):
            self.assertContains(self.upload(name), 'Tipo de arquivo não aceito')
        self.assertFalse(Material.objects.exists())

    def test_pdf_abre_e_documento_e_baixado(self):
        self.upload('slides.pdf', b'%PDF-1.4')
        self.upload('apostila.docx')
        self.upload('foto.png', png())
        pdf, docx, image = (
            self.client.get(reverse('material_open', args=[m.pk])) for m in Material.objects.order_by('id')
        )
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf['Content-Disposition'].startswith('inline'))
        self.assertEqual(docx['Content-Type'], 'application/octet-stream')
        self.assertTrue(docx['Content-Disposition'].startswith('attachment'))
        self.assertIn('sandbox', image['Content-Security-Policy'])
        self.assertEqual(image['X-Content-Type-Options'], 'nosniff')


class LateCheckinTests(TestCase):
    """A3 e A4: leituras offline enviadas depois e presença esquecida após finalizar."""

    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        start = now() - timedelta(hours=3)
        self.activity = make_activity(
            self.org, start_date=start, end_date=start + timedelta(hours=8),
            registration_start=start - timedelta(days=2), registration_end=start,
        )  # fmt: skip
        self.session = make_session(
            self.activity, start_at=start, end_at=start + timedelta(hours=2), check_in_open=False
        )
        self.reg = Registration.objects.create(user=make_user('ana'), activity=self.activity)
        self.client.force_login(self.org)

    def scan(self, **extra):
        body = {'token': f'ARENA-EVENTS:{self.reg.qr_token}', **extra}
        return self.client.post(
            reverse('qr_checkin', args=[self.session.id]), json.dumps(body), content_type='application/json'
        )

    def test_leitura_offline_vale_mesmo_com_checkin_fechado(self):
        self.assertEqual(self.scan().status_code, 400)  # leitura ao vivo com check-in fechado: recusada
        during = (self.session.start_at + timedelta(minutes=10)).isoformat()
        self.assertEqual(self.scan(offline=True, scanned_at=during).status_code, 200)
        self.assertTrue(Attendance.objects.filter(registration=self.reg).exists())

    def test_leitura_offline_fora_do_horario_e_recusada(self):
        long_before = (self.session.start_at - timedelta(days=1)).isoformat()
        self.assertEqual(self.scan(offline=True, scanned_at=long_before).status_code, 400)
        self.assertEqual(self.scan(offline=True, scanned_at='lixo').status_code, 400)

    def test_atividade_finalizada(self):
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.FINISHED)
        during = (self.session.start_at + timedelta(minutes=10)).isoformat()
        self.assertEqual(self.scan(offline=True, scanned_at=during).status_code, 200)  # sincronizou depois
        Attendance.objects.all().delete()
        # presença esquecida lançada à mão pela equipe
        self.client.post(reverse('manual_checkin', args=[self.session.id, self.reg.id]))
        self.assertTrue(Attendance.objects.filter(registration=self.reg).exists())
        page = self.client.get(reverse('session_attendance', args=[self.session.id]))
        self.assertContains(page, 'Emitir certificados pendentes')

    def test_atividade_cancelada_continua_bloqueada(self):
        Activity.objects.filter(pk=self.activity.pk).update(status=Activity.Status.CANCELLED)
        self.client.post(reverse('manual_checkin', args=[self.session.id, self.reg.id]))
        self.assertFalse(Attendance.objects.exists())

    def test_sessao_expirada_redireciona_sem_json(self):
        self.client.logout()
        self.assertEqual(self.scan().status_code, 302)


class EmailBatchTests(TestCase):
    """A5: um destinatário recusado não impede os outros."""

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_falha_em_um_nao_para_os_outros(self):
        from django.core.mail.backends.locmem import EmailBackend

        original = EmailBackend.send_messages

        def flaky(self, messages):
            if messages[0].to == ['recusado@exemplo.com']:
                raise Exception('550 recipient rejected')
            return original(self, messages)

        messages = [EmailMessage('A', 'x', to=[addr]) for addr in ('a@x.com', 'recusado@exemplo.com', 'c@x.com')]
        with mock.patch.object(EmailBackend, 'send_messages', flaky):
            sent = notifications.send_messages(messages)
        self.assertEqual(sent, 2)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['a@x.com', 'c@x.com'])


class SecretKeyTests(TestCase):
    """A7: a chave de exemplo só sobe em desenvolvimento local."""

    def run_check(self, **env):
        base = {k: v for k, v in os.environ.items() if k not in ('SECRET_KEY', 'DEBUG', 'ALLOWED_HOSTS')}
        result = subprocess.run(
            [sys.executable, 'manage.py', 'check'],
            cwd=settings.BASE_DIR, env={**base, **env}, capture_output=True, text=True, timeout=120,
        )  # fmt: skip
        return result.returncode, result.stderr

    def test_regras(self):
        placeholder = 'replace-with-a-long-random-secret'
        self.assertEqual(self.run_check(SECRET_KEY=placeholder, DEBUG='True')[0], 0)  # na própria máquina
        code, err = self.run_check(SECRET_KEY=placeholder, DEBUG='True', ALLOWED_HOSTS='eventos.fsa.br')
        self.assertNotEqual(code, 0)
        self.assertIn('Defina uma SECRET_KEY', err)
        self.assertNotEqual(self.run_check(SECRET_KEY=placeholder, DEBUG='False')[0], 0)
        self.assertNotEqual(self.run_check(SECRET_KEY='curta', DEBUG='False')[0], 0)
        self.assertNotEqual(self.run_check(SECRET_KEY='y' * 40, DEBUG='False', ALLOWED_HOSTS='eventos.fsa.br')[0], 0)
        self.assertEqual(self.run_check(SECRET_KEY='x' * 50, DEBUG='False', ALLOWED_HOSTS='eventos.fsa.br')[0], 0)


class ProfileTests(TestCase):
    """B1: meus dados e trocar senha."""

    def setUp(self):
        self.user = make_user('ana', first_name='ana', last_name='silva')
        make_user('bia')
        self.client.force_login(self.user)

    def test_editar_dados(self):
        url = reverse('profile')
        self.assertContains(self.client.get(url), 'aparece nos certificados')
        self.client.post(url, {'first_name': 'Ana', 'last_name': 'da Silva', 'email': 'ana@exemplo.com'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.get_full_name(), 'Ana da Silva')
        response = self.client.post(url, {'first_name': 'Ana', 'last_name': 'da Silva', 'email': 'bia@exemplo.com'})
        self.assertNotContains(response, 'Já existe', status_code=302)  # SEC-09: não revela a conta da Bia
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'ana@exemplo.com')

    def test_trocar_senha_sem_sair(self):
        self.assertContains(self.client.get(reverse('my_registrations')), reverse('password_change'))
        response = self.client.post(
            reverse('password_change'),
            {'old_password': PASSWORD, 'new_password1': 'Outra!senha987', 'new_password2': 'Outra!senha987'},
            follow=True,
        )
        self.assertContains(response, 'Senha alterada.')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Outra!senha987'))


class ErrorPageTests(TestCase):
    """B2: páginas de erro com menu, em português."""

    def test_404_e_403(self):
        page = self.client.get('/eventos/nao-existe/')
        self.assertEqual(page.status_code, 404)
        self.assertContains(page, 'Página não encontrada', status_code=404)
        self.assertContains(page, 'Validar certificado', status_code=404)
        self.client.force_login(make_user('ana'))
        page = self.client.get(reverse('organizer_dashboard'))
        self.assertContains(page, 'Acesso restrito a organizadores e administradores.', status_code=403)
        self.assertContains(page, 'Ir para o início', status_code=403)

    def test_500_independe_do_contexto(self):
        from django.template.loader import get_template

        html = get_template('500.html').render({})
        self.assertIn('Algo deu errado', html)


@override_settings(EMAIL_BACKEND=LOCMEM)
class CancelConfirmationTests(TestCase):
    """B3: cancelar pede confirmação."""

    def test_confirmar_antes(self):
        activity = make_activity(make_user('org', User.Role.ORGANIZER), capacity=1)
        ana = make_user('ana')
        reg = Registration.objects.create(user=ana, activity=activity)
        Registration.objects.create(user=make_user('bia'), activity=activity, status='WAITLISTED', waitlisted_at=now())
        self.client.force_login(ana)
        url = reverse('cancel_registration', args=[reg.pk])
        self.assertContains(self.client.get(reverse('my_registrations')), url)
        page = self.client.get(url)
        self.assertContains(page, 'passa <strong>na hora</strong> para a próxima pessoa')
        reg.refresh_from_db()
        self.assertEqual(reg.status, 'CONFIRMED')  # só abrir a página não cancela
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url)
        reg.refresh_from_db()
        self.assertEqual(reg.status, 'CANCELLED')


@override_settings(EMAIL_BACKEND=LOCMEM)
class StatusTransitionTests(TestCase):
    """B4 e B5: mudanças de situação permitidas e finalizar com trava."""

    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.client.force_login(self.org)

    def edit(self, activity, status):
        from activities.test_utils import local

        data = {
            'title': activity.title, 'description': 'd', 'activity_type': 'EVENT',
            'start_date': local(activity.start_date), 'end_date': local(activity.end_date),
            'registration_start': local(activity.registration_start),
            'registration_end': local(activity.registration_end), 'capacity': activity.capacity,
            'location': '', 'status': status, 'waitlist_enabled': 'on', 'access_code': '',
            'workload_hours': '', 'min_attendance_percent': 75,
        }  # fmt: skip
        return self.client.post(reverse('activity_edit', args=[activity.pk]), data)

    def test_opcoes_por_situacao(self):
        draft = make_activity(self.org, status='DRAFT')
        form = self.client.get(reverse('activity_edit', args=[draft.pk])).context['form']
        self.assertEqual({v for v, _ in form.fields['status'].choices}, {'DRAFT', 'PUBLISHED', 'CANCELLED'})
        self.assertContains(self.edit(draft, 'FINISHED'), 'Faça uma escolha válida')
        finished = make_activity(self.org, status='FINISHED')
        form = self.client.get(reverse('activity_edit', args=[finished.pk])).context['form']
        self.assertEqual([v for v, _ in form.fields['status'].choices], ['FINISHED'])
        self.edit(finished, 'CANCELLED')
        finished.refresh_from_db()
        self.assertEqual(finished.status, 'FINISHED')

    def test_publicada_com_inscritos_nao_volta_a_rascunho(self):
        activity = make_activity(self.org)
        Registration.objects.create(user=make_user('ana'), activity=activity)
        self.edit(activity, 'DRAFT')
        activity.refresh_from_db()
        self.assertEqual(activity.status, 'PUBLISHED')

    def test_finalizar_duas_vezes(self):
        activity = make_activity(self.org, min_attendance_percent=50)
        session = make_session(activity)
        reg = Registration.objects.create(user=make_user('ana'), activity=activity)
        Attendance.objects.create(registration=reg, session=session, validated_by=self.org)
        url = reverse('activity_finish', args=[activity.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url)
        # segundo clique (ou outra pessoa) com a atividade já finalizada: nada em dobro, sem erro
        from certificates.services import finish_activity

        self.assertIsNone(finish_activity(activity))
        self.assertEqual(Certificate.objects.count(), 1)

    def test_formulario_desatualizado(self):
        activity = make_activity(self.org)
        Activity.objects.filter(pk=activity.pk).update(status='CANCELLED')  # outra pessoa cancelou
        stale = Activity.objects.get(pk=activity.pk)
        stale.status = 'PUBLISHED'  # o formulário foi aberto antes
        with mock.patch('activities.views.manage.get_object_or_404', return_value=stale):
            response = self.edit(stale, 'FINISHED')
        self.assertRedirects(response, reverse('activity_edit', args=[activity.pk]))
        self.assertEqual(Activity.objects.get(pk=activity.pk).status, 'CANCELLED')
        self.assertFalse(Certificate.objects.exists())


class PrivateCertificateImagesTests(PrivateFilesMixin, TestCase):
    """B9: assinatura e arte de fundo fora do /media/ público."""

    def setUp(self):
        self.use_private_root()

    def test_assinatura_privada(self):
        site = SiteSettings.load()
        site.signature_image = SimpleUploadedFile('assinatura.png', png())
        site.save()
        path = Path(site.signature_image.path)
        self.assertTrue(str(path).startswith(self.private))
        self.assertFalse(str(path).startswith(str(settings.MEDIA_ROOT)))
        url = reverse('settings_private_image', args=['assinatura'])
        self.client.force_login(make_user('ana', User.Role.ORGANIZER))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(make_user('adm', User.Role.ADMIN))
        response = self.client.get(url)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertContains(self.client.get(reverse('site_settings')), url)
        self.assertEqual(self.client.get(reverse('settings_private_image', args=['outra'])).status_code, 404)

    def test_migracao_move_arquivo_existente(self):
        import importlib

        from django.apps import apps

        public = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, public, ignore_errors=True)
        (Path(public) / 'institution').mkdir()
        (Path(public) / 'institution' / 'fundo.png').write_bytes(png())
        SiteSettings.objects.update_or_create(pk=1, defaults={'certificate_background': 'institution/fundo.png'})
        migration = importlib.import_module('core.migrations.0007_private_certificate_images')
        with override_settings(MEDIA_ROOT=public):
            migration.move_to_private(apps, None)
        self.assertTrue((Path(self.private) / 'institution' / 'fundo.png').exists())
        self.assertFalse((Path(public) / 'institution' / 'fundo.png').exists())


class CsvInjectionTests(TestCase):
    """B10: células que o Excel trataria como fórmula viram texto."""

    def test_celula(self):
        self.assertEqual(safe_cell('=HYPERLINK("x")'), '\'=HYPERLINK("x")')
        self.assertEqual(safe_cell('+5511999'), "'+5511999")
        self.assertEqual(safe_cell('@SUM(A1)'), "'@SUM(A1)")
        self.assertEqual(safe_cell('-'), '-')
        self.assertEqual(safe_cell('Ana'), 'Ana')
        self.assertEqual(safe_cell(-3), -3)

    def test_exportacao_da_pesquisa(self):
        org = make_user('org', User.Role.ORGANIZER)
        activity = make_activity(org)
        reg = Registration.objects.create(user=make_user('ana'), activity=activity)
        SurveyResponse.objects.create(registration=reg, score=3, comment='=HYPERLINK("http://x")')
        self.client.force_login(org)
        response = self.client.get(reverse('survey_csv', args=[activity.id]))
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertEqual(rows[1][2], '\'=HYPERLINK("http://x")')
