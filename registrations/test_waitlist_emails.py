from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from activities.models import Activity, RegistrationQuestion
from activities.test_utils import local, make_activity, make_user, now
from core.models import OutgoingEmail

from .models import Registration, RegistrationAnswer


class WaitlistTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, capacity=1)
        self.url = reverse('register_activity', args=[self.activity.id])

    def register(self, user):
        self.client.force_login(user)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, follow=True)

    def test_entra_na_fila_quando_lota_e_e_promovido_ao_cancelar(self):
        ana, bia, caio = make_user('ana'), make_user('bia'), make_user('caio')
        self.register(ana)
        response = self.register(bia)
        self.assertContains(response, 'lista de espera na posição 1')
        response = self.register(caio)
        self.assertContains(response, 'posição 2')
        bia_reg = Registration.objects.get(user=bia)
        self.assertEqual(bia_reg.status, Registration.Status.WAITLISTED)

        # Página da atividade mostra a fila e a posição
        self.client.force_login(caio)
        self.assertContains(
            self.client.get(reverse('activity_detail', args=[self.activity.id])), 'posição <strong>2</strong>'
        )
        self.assertContains(self.client.get(reverse('my_registrations')), 'Sair da lista de espera')

        # Ana cancela: Bia (primeira da fila) é confirmada e recebe o QR por e-mail
        mail.outbox.clear()
        self.client.force_login(ana)
        ana_reg = Registration.objects.get(user=ana)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cancel_registration', args=[ana_reg.id]))
        bia_reg.refresh_from_db()
        self.assertEqual(bia_reg.status, Registration.Status.CONFIRMED)
        self.assertEqual(Registration.objects.get(user=caio).waitlist_position(), 1)
        subjects = {m.to[0]: m.subject for m in mail.outbox}
        self.assertIn('cancelada', subjects['ana@exemplo.com'])
        self.assertIn('abriu uma vaga', subjects['bia@exemplo.com'])
        promoted = next(m for m in mail.outbox if m.to == ['bia@exemplo.com'])
        self.assertEqual(promoted.attachments[0][2], 'image/png')

    def test_sair_da_fila_nao_promove_ninguem(self):
        ana, bia = make_user('ana'), make_user('bia')
        self.register(ana)
        self.register(bia)
        self.client.force_login(bia)
        self.client.post(reverse('cancel_registration', args=[Registration.objects.get(user=bia).id]))
        self.assertEqual(Registration.objects.get(user=ana).status, Registration.Status.CONFIRMED)
        self.assertEqual(Registration.objects.get(user=bia).status, Registration.Status.CANCELLED)

    def test_aumentar_capacidade_chama_a_fila(self):
        self.register(make_user('ana'))
        self.register(make_user('bia'))
        self.client.force_login(self.org)
        a = self.activity
        data = {
            'title': a.title, 'description': a.description, 'activity_type': a.activity_type,
            'start_date': local(a.start_date), 'end_date': local(a.end_date),
            'registration_start': local(a.registration_start), 'registration_end': local(a.registration_end),
            'capacity': 2, 'location': '', 'status': a.status, 'waitlist_enabled': 'on',
            'certificate_enabled': 'on', 'min_attendance_percent': 75,
        }  # fmt: skip
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('activity_edit', args=[a.id]), data, follow=True)
        self.assertContains(response, '1 pessoa(s) da lista de espera foram confirmadas')
        self.assertFalse(Registration.objects.filter(status=Registration.Status.WAITLISTED).exists())


class QuestionsTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org)
        self.course = RegistrationQuestion.objects.create(
            activity=self.activity, label='Curso', kind='CHOICE', choices='ADS\nGTI\nRedes', required=True, order=1
        )
        self.access = RegistrationQuestion.objects.create(
            activity=self.activity, label='Precisa de acessibilidade?', kind='YES_NO', order=2
        )

    def test_organizador_cria_pergunta(self):
        self.client.force_login(self.org)
        url = reverse('question_create', args=[self.activity.id])
        response = self.client.post(url, {'label': 'Semestre', 'kind': 'CHOICE', 'choices': 'só uma', 'order': 3})
        self.assertContains(response, 'pelo menos duas opções')
        response = self.client.post(url, {'label': 'Semestre', 'kind': 'TEXT', 'choices': '', 'order': 3})
        self.assertEqual(response.status_code, 302)
        manage = self.client.get(reverse('activity_manage', args=[self.activity.id]))
        self.assertContains(manage, 'Semestre')
        self.assertContains(manage, 'ADS, GTI, Redes')

    def test_inscricao_exige_e_grava_respostas(self):
        user = make_user('ana')
        self.client.force_login(user)
        detail = self.client.get(reverse('activity_detail', args=[self.activity.id]))
        self.assertContains(detail, 'Precisa de acessibilidade?')
        url = reverse('register_activity', args=[self.activity.id])
        response = self.client.post(url, {f'q_{self.access.id}': 'Sim'})
        self.assertContains(response, 'Confira os dados do formulário')
        self.assertFalse(Registration.objects.exists())
        self.assertContains(response, 'value="Sim" selected')  # o que já foi respondido continua preenchido
        self.client.post(url, {f'q_{self.course.id}': 'GTI', f'q_{self.access.id}': 'Sim'})
        registration = Registration.objects.get()
        answers = dict(RegistrationAnswer.objects.values_list('question__label', 'value'))
        self.assertEqual(answers, {'Curso': 'GTI', 'Precisa de acessibilidade?': 'Sim'})
        # cancela e volta: respostas anteriores aparecem preenchidas
        self.client.post(reverse('cancel_registration', args=[registration.id]))
        self.assertContains(
            self.client.get(reverse('activity_detail', args=[self.activity.id])), 'value="GTI" selected'
        )

    def test_opcao_invalida(self):
        self.client.force_login(make_user('ana'))
        response = self.client.post(
            reverse('register_activity', args=[self.activity.id]), {f'q_{self.course.id}': 'Medicina'}
        )
        self.assertContains(response, 'Faça uma escolha válida')


class EmailTests(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER)
        self.activity = make_activity(self.org, title='Oficina de Linux')

    def test_confirmacao_com_qr_anexo(self):
        self.client.force_login(make_user('ana'))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('register_activity', args=[self.activity.id]))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn('inscrição confirmada em Oficina de Linux', message.subject)
        self.assertIn('Oficina de Linux', message.body)
        self.assertEqual(message.attachments[0][0], 'qrcode-inscricao.png')
        self.assertTrue(message.attachments[0][1].startswith(b'\x89PNG'))

    @override_settings(EMAIL_BACKEND='registrations.test_waitlist_emails.BrokenBackend')
    def test_falha_no_email_nao_impede_inscricao(self):
        self.client.force_login(make_user('ana'))
        with self.assertLogs('core.outbox', 'WARNING'), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('register_activity', args=[self.activity.id]), follow=True)
        self.assertContains(response, 'Inscrição confirmada')
        self.assertTrue(Registration.objects.exists())
        # o e-mail fica na fila para uma nova tentativa, sem perder a mensagem
        queued = OutgoingEmail.objects.get()
        self.assertEqual((queued.status, queued.attempts), (OutgoingEmail.Status.PENDING, 1))
        self.assertIn('fora do ar', queued.last_error)

    def test_cancelamento_da_atividade_avisa_inscritos_e_fila(self):
        Registration.objects.create(user=make_user('ana'), activity=self.activity)
        Registration.objects.create(
            user=make_user('bia'), activity=self.activity, status=Registration.Status.WAITLISTED, waitlisted_at=now()
        )
        Registration.objects.create(
            user=make_user('caio'), activity=self.activity, status=Registration.Status.CANCELLED
        )
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('activity_cancel', args=[self.activity.id]))
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['ana@exemplo.com', 'bia@exemplo.com'])
        self.assertIn('atividade cancelada', mail.outbox[0].subject)

    def test_lembrete_de_vespera_uma_vez(self):
        soon = make_activity(
            self.org, title='Amanhã', start_date=now() + timedelta(hours=20), end_date=now() + timedelta(hours=22),
            registration_end=now() + timedelta(hours=19),
        )  # fmt: skip
        later = make_activity(self.org, title='Semana que vem', start_date=now() + timedelta(days=7),
                              end_date=now() + timedelta(days=8))  # fmt: skip
        Registration.objects.create(user=make_user('ana'), activity=soon)
        Registration.objects.create(user=make_user('bia'), activity=soon, status=Registration.Status.CANCELLED)
        Registration.objects.create(user=make_user('caio'), activity=later)
        out = StringIO()
        call_command('send_reminders', '--simular', stdout=out)
        self.assertIn('ana@exemplo.com', out.getvalue())
        self.assertEqual(len(mail.outbox), 0)
        call_command('send_reminders', stdout=StringIO())
        self.assertEqual([m.to[0] for m in mail.outbox], ['ana@exemplo.com'])
        self.assertIn('lembrete', mail.outbox[0].subject)
        call_command('send_reminders', stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)  # não repete

    def test_atividade_rascunho_nao_recebe_lembrete(self):
        draft = make_activity(self.org, status=Activity.Status.DRAFT, start_date=now() + timedelta(hours=5))
        Registration.objects.create(user=make_user('ana'), activity=draft)
        call_command('send_reminders', stdout=StringIO())
        self.assertEqual(len(mail.outbox), 0)


class BrokenBackend:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        raise ConnectionRefusedError('servidor SMTP fora do ar')

    def open(self):
        raise ConnectionRefusedError('servidor SMTP fora do ar')

    def __exit__(self, *args):
        return False
