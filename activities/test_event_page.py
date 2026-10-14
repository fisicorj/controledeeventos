import io
import os

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from registrations.models import Registration

from .calendar import map_url
from .models import Activity, Announcement, Speaker
from .test_utils import make_activity, make_session, make_user


def png(size=(4, 4), noise=False):
    from PIL import Image

    image = Image.frombytes('RGB', size, os.urandom(size[0] * size[1] * 3)) if noise else Image.new('RGB', size, 'navy')
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


PNG = png()


@override_settings(SITE_URL='https://eventos.fsa.br')
class EventPageTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(
            self.org, title='Semana de Engenharia 2026', location='Auditório FSA, São Bernardo do Campo'
        )

    def test_endereco_amigavel(self):
        self.assertEqual(self.activity.slug, 'semana-de-engenharia-2026')
        self.assertEqual(self.activity.get_absolute_url(), '/eventos/semana-de-engenharia-2026/')
        other = make_activity(self.org, title='Semana de Engenharia 2026')
        self.assertEqual(other.slug, 'semana-de-engenharia-2026-2')
        # mudar o título não quebra links já compartilhados
        self.activity.title = 'Outro nome'
        self.activity.save()
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.slug, 'semana-de-engenharia-2026')
        self.assertContains(self.client.get('/eventos/semana-de-engenharia-2026/'), 'Outro nome')
        # o endereço antigo continua funcionando e aponta o novo como canônico
        old = self.client.get(reverse('activity_detail', args=[self.activity.pk]))
        self.assertContains(
            old, '<link rel="canonical" href="https://eventos.fsa.br/eventos/semana-de-engenharia-2026/">'
        )

    def test_rascunho_nao_aparece_pelo_endereco_amigavel(self):
        draft = make_activity(self.org, title='Rascunho', status=Activity.Status.DRAFT)
        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(reverse('event_calendar', args=[draft.slug])).status_code, 404)

    def test_divulgacao(self):
        self.activity.image = SimpleUploadedFile('capa.png', PNG, content_type='image/png')
        self.activity.save()
        page = self.client.get(self.activity.get_absolute_url())
        self.assertContains(page, '<meta property="og:title" content="Semana de Engenharia 2026">')
        self.assertContains(page, 'property="og:image" content="https://eventos.fsa.br/media/activities/capa')
        self.assertContains(page, 'https://wa.me/?text=Semana%20de%20Engenharia%202026')
        self.assertContains(page, 'linkedin.com/sharing')
        self.assertContains(page, 'calendar.google.com/calendar/render?action=TEMPLATE')
        self.assertContains(page, 'outlook.office.com/calendar')
        self.assertContains(page, reverse('event_calendar', args=[self.activity.slug]))
        self.assertContains(page, 'google.com/maps/search/?api=1&amp;query=Audit%C3%B3rio%20FSA')

    def test_mapa_so_para_local_fisico(self):
        self.assertEqual(map_url(''), '')
        self.assertEqual(map_url('Online pelo Teams'), '')
        self.assertTrue(map_url('Sala 12').startswith('https://www.google.com/maps/'))

    def test_arquivo_ics(self):
        make_session(self.activity, title='Abertura; boas-vindas')
        make_session(self.activity, title='Oficina, prática')
        response = self.client.get(reverse('event_calendar', args=[self.activity.slug]))
        self.assertEqual(response['Content-Type'], 'text/calendar; charset=utf-8')
        text = response.content.decode()
        self.assertTrue(text.startswith('BEGIN:VCALENDAR\r\n'))
        self.assertEqual(text.count('BEGIN:VEVENT'), 2)  # um por sessão
        self.assertIn(r'SUMMARY:Semana de Engenharia 2026: Abertura\; boas-vindas', text)
        self.assertIn(r'Oficina\, prática', text)
        self.assertIn('URL:https://eventos.fsa.br/eventos/semana-de-engenharia-2026/', text)
        self.assertTrue(all(len(line.encode()) <= 75 for line in text.split('\r\n')))

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_confirmacao_leva_o_ics(self):
        self.client.force_login(make_user('ana'))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('register_activity', args=[self.activity.id]))
        names = [name for name, _, _ in mail.outbox[0].attachments]
        self.assertEqual(names, ['qrcode-inscricao.png', 'semana-de-engenharia-2026.ics'])
        self.assertIn('https://eventos.fsa.br/eventos/semana-de-engenharia-2026/', mail.outbox[0].body)


class SpeakerTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.client.force_login(self.org)

    def test_cadastro_e_exibicao(self):
        response = self.client.post(
            reverse('speaker_create', args=[self.activity.id]),
            {'name': 'Ana Lima', 'role': 'Professora da Fatec', 'bio': 'Pesquisa redes.', 'link': '', 'order': 1,
             'photo': SimpleUploadedFile('ana.png', PNG, content_type='image/png')},
        )  # fmt: skip
        self.assertRedirects(response, reverse('activity_manage', args=[self.activity.id]))
        speaker = Speaker.objects.get()
        session = make_session(self.activity, title='Palestra de abertura')
        form = self.client.get(reverse('session_edit', args=[session.id]))
        self.assertContains(form, 'Ana Lima')
        session.speakers.add(speaker)
        page = self.client.get(self.activity.get_absolute_url())
        self.assertContains(page, 'Palestrante</h2>')
        self.assertContains(page, 'Professora da Fatec')
        self.assertContains(page, 'Pesquisa redes.')
        self.assertContains(page, 'bi-mic')  # nome do palestrante na programação

    def test_foto_grande_recusada(self):
        big = SimpleUploadedFile('g.png', png((1000, 1000), noise=True), content_type='image/png')  # ~3 MB
        response = self.client.post(
            reverse('speaker_create', args=[self.activity.id]), {'name': 'X', 'order': 1, 'photo': big}
        )
        self.assertContains(response, 'no máximo 2 MB')

    def test_permissao(self):
        speaker = Speaker.objects.create(activity=self.activity, name='Ana')
        self.client.force_login(make_user('outro', User.Role.ORGANIZER))
        for url in (reverse('speaker_create', args=[self.activity.id]), reverse('speaker_edit', args=[speaker.id]),
                    reverse('speaker_delete', args=[speaker.id])):  # fmt: skip
            self.assertEqual(self.client.get(url).status_code, 403)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AnnouncementTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER, first_name='Manoel')
        self.activity = make_activity(self.org, capacity=1)
        self.ana, self.bia = make_user('ana'), make_user('bia')
        Registration.objects.create(user=self.ana, activity=self.activity)
        Registration.objects.create(user=self.bia, activity=self.activity, status=Registration.Status.WAITLISTED)
        self.url = reverse('announcement_create', args=[self.activity.id])
        self.client.force_login(self.org)

    def send(self, **data):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                self.url, {'audience': 'CONFIRMED', 'subject': 'Mudança de sala', 'body': 'Agora na sala 12.',
                           'show_on_page': 'on', **data}, follow=True,
            )  # fmt: skip

    def test_envio_para_confirmados(self):
        self.assertContains(self.client.get(self.url), '1 confirmado, 1 na lista de espera')
        response = self.send()
        self.assertContains(response, 'Comunicado na fila de envio para 1 pessoa.')
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['ana@exemplo.com'])
        self.assertIn('Mudança de sala', message.subject)
        self.assertIn('Agora na sala 12.', message.body)
        self.assertEqual(message.reply_to, ['org@exemplo.com'])
        announcement = Announcement.objects.get()
        self.assertEqual((announcement.recipients_count, announcement.sent_by), (1, self.org))
        self.assertContains(response, 'Mudança de sala')  # histórico na página de gestão

    def test_todos(self):
        self.send(audience='ALL')
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['ana@exemplo.com', 'bia@exemplo.com'])

    def test_aviso_na_pagina_so_para_o_publico_certo(self):
        self.send()
        page_url = self.activity.get_absolute_url()
        self.assertNotContains(self.client.get(page_url), 'Agora na sala 12.')  # organizador não inscrito
        self.client.logout()
        self.assertNotContains(self.client.get(page_url), 'Agora na sala 12.')
        self.client.force_login(self.bia)  # lista de espera: comunicado era só para confirmados
        self.assertNotContains(self.client.get(page_url), 'Agora na sala 12.')
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(page_url), 'Avisos da organização')

    def test_grupo_vazio(self):
        Registration.objects.filter(user=self.bia).delete()
        response = self.send(audience='WAITLISTED')
        self.assertContains(response, 'Não há ninguém neste grupo')
        self.assertFalse(Announcement.objects.exists())

    def test_permissao(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(self.url).status_code, 403)
