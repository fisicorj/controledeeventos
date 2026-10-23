"""Inscrição por sessão: escolha das sessões, vagas e fila por sessão, check-in e certificado por sessão."""

import json
from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from activities.models import Activity, Session
from activities.test_utils import local, make_activity, make_session, make_user
from attendance.models import Attendance
from certificates.models import Certificate
from certificates.services import finish_activity

from . import sessions
from .models import Registration, SessionEnrollment

S = SessionEnrollment.Status


class PerSessionSetup(TestCase):
    def setUp(self):
        self.org = make_user('org', User.Role.ORGANIZER, first_name='Olga')
        self.activity = make_activity(
            self.org, title='Semana de TI', activity_type=Activity.Type.EVENT, capacity=10, per_session=True
        )
        start = self.activity.start_date
        self.talk1 = make_session(
            self.activity, title='Palestra de IA', start_at=start, end_at=start + timedelta(hours=1), capacity=1
        )
        self.talk2 = make_session(
            self.activity,
            title='Palestra de Redes',
            start_at=start + timedelta(hours=2),
            end_at=start + timedelta(hours=4),
            capacity=5,
        )
        # Mesmo horário da palestra de IA
        self.clash = make_session(
            self.activity,
            title='Oficina de Python',
            start_at=start + timedelta(minutes=30),
            end_at=start + timedelta(hours=1, minutes=30),
            capacity=5,
        )
        self.url = reverse('register_activity', args=[self.activity.id])

    def register(self, user, *chosen):
        self.client.force_login(user)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, {'sessions': [s.pk for s in chosen]}, follow=True)

    def enrollment(self, user, session):
        return SessionEnrollment.objects.get(registration__user=user, session=session)


class ChoiceTests(PerSessionSetup):
    def test_inscricao_com_sessoes_e_fila_da_sessao(self):
        ana, bia = make_user('ana'), make_user('bia')
        response = self.register(ana, self.talk1, self.talk2)
        self.assertContains(response, 'Sessões confirmadas: Palestra de IA, Palestra de Redes.')
        self.assertEqual(self.enrollment(ana, self.talk1).status, S.CONFIRMED)
        email = next(m for m in mail.outbox if m.to == ['ana@exemplo.com'])
        self.assertIn('Palestra de IA', email.body)
        self.assertIn(reverse('registration_sessions', args=[Registration.objects.get(user=ana).pk]), email.body)

        response = self.register(bia, self.talk1)
        self.assertContains(response, 'Na lista de espera: Palestra de IA (posição 1).')
        self.assertEqual(self.enrollment(bia, self.talk1).status, S.WAITLISTED)
        # A inscrição no evento continua confirmada (as vagas do evento são outras).
        self.assertEqual(Registration.objects.get(user=bia).status, Registration.Status.CONFIRMED)

    def test_precisa_escolher_ao_menos_uma_sessao(self):
        response = self.register(make_user('ana'))
        self.assertContains(response, 'Escolha pelo menos uma sessão.')
        self.assertFalse(Registration.objects.exists())

    def test_sessoes_no_mesmo_horario_sao_recusadas(self):
        response = self.register(make_user('ana'), self.talk1, self.clash)
        self.assertContains(response, 'acontecem no mesmo horário')
        self.assertFalse(Registration.objects.exists())

    def test_sair_da_sessao_chama_a_fila_e_avisa(self):
        ana, bia = make_user('ana'), make_user('bia')
        self.register(ana, self.talk1)
        self.register(bia, self.talk1)
        mail.outbox.clear()
        self.client.force_login(ana)
        page = reverse('registration_sessions', args=[Registration.objects.get(user=ana).pk])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(page, {'session': self.talk1.pk, 'action': 'leave'}, follow=True)
        self.assertContains(response, 'Você saiu de')
        self.assertEqual(self.enrollment(ana, self.talk1).status, S.CANCELLED)
        self.assertEqual(self.enrollment(bia, self.talk1).status, S.CONFIRMED)
        self.assertEqual([m.to for m in mail.outbox], [['bia@exemplo.com']])
        self.assertIn('abriu uma vaga na sessão Palestra de IA', mail.outbox[0].subject)

    def test_trocar_sessao_depois_da_inscricao(self):
        ana = make_user('ana')
        self.register(ana, self.talk1)
        self.client.force_login(ana)
        page = reverse('registration_sessions', args=[Registration.objects.get(user=ana).pk])
        response = self.client.get(page)
        self.assertContains(response, 'Sair da sessão')
        response = self.client.post(page, {'session': self.clash.pk, 'action': 'enter'}, follow=True)
        self.assertContains(response, 'no mesmo horário')
        self.client.post(page, {'session': self.talk1.pk, 'action': 'leave'})
        response = self.client.post(page, {'session': self.clash.pk, 'action': 'enter'}, follow=True)
        self.assertContains(response, 'Você está confirmado em')
        self.assertEqual(self.enrollment(ana, self.clash).status, S.CONFIRMED)

    def test_nao_muda_depois_que_a_sessao_comeca(self):
        ana = make_user('ana')
        self.register(ana, self.talk1)
        Session.objects.filter(pk=self.talk1.pk).update(start_at=timezone.now() - timedelta(minutes=5))
        self.client.force_login(ana)
        page = reverse('registration_sessions', args=[Registration.objects.get(user=ana).pk])
        response = self.client.post(page, {'session': self.talk1.pk, 'action': 'leave'}, follow=True)
        self.assertContains(response, 'já começou')
        self.assertEqual(self.enrollment(ana, self.talk1).status, S.CONFIRMED)

    def test_cancelar_a_inscricao_libera_as_sessoes(self):
        ana, bia = make_user('ana'), make_user('bia')
        self.register(ana, self.talk1, self.talk2)
        self.register(bia, self.talk1)
        self.client.force_login(ana)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cancel_registration', args=[Registration.objects.get(user=ana).pk]))
        self.assertFalse(SessionEnrollment.objects.filter(registration__user=ana, status__in=sessions.ACTIVE).exists())
        self.assertEqual(self.enrollment(bia, self.talk1).status, S.CONFIRMED)

    def test_fila_do_evento_escolhe_as_sessoes_depois(self):
        Activity.objects.filter(pk=self.activity.pk).update(capacity=1, waitlist_offer_hours=0)
        ana, bia = make_user('ana'), make_user('bia')
        self.register(ana, self.talk2)
        response = self.register(bia, self.talk2)
        self.assertContains(response, 'escolha as sessões em')
        bia_reg = Registration.objects.get(user=bia)
        self.assertEqual(bia_reg.status, Registration.Status.WAITLISTED)
        self.assertFalse(bia_reg.session_enrollments.exists())
        page = reverse('registration_sessions', args=[bia_reg.pk])
        self.client.force_login(bia)
        response = self.client.post(page, {'session': self.talk2.pk, 'action': 'enter'}, follow=True)
        self.assertContains(response, 'precisa estar confirmada')

        self.client.force_login(ana)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('cancel_registration', args=[Registration.objects.get(user=ana).pk]))
        bia_reg.refresh_from_db()
        self.assertEqual(bia_reg.status, Registration.Status.CONFIRMED)
        self.client.force_login(bia)
        self.client.post(page, {'session': self.talk2.pk, 'action': 'enter'})
        self.assertEqual(self.enrollment(bia, self.talk2).status, S.CONFIRMED)

    def test_paginas_mostram_vagas_e_escolhas(self):
        ana = make_user('ana')
        self.client.force_login(ana)
        response = self.client.get(self.activity.get_absolute_url())
        self.assertContains(response, 'Sessões que você vai assistir')
        self.assertContains(response, '1 de 1 vaga')
        self.register(ana, self.talk1)
        response = self.client.get(self.activity.get_absolute_url())
        self.assertContains(response, 'Você vai')
        self.assertContains(response, 'Escolher ou trocar sessões')
        self.assertContains(self.client.get(reverse('my_registrations')), '1 sessão escolhida')

    def test_outra_pessoa_nao_ve_as_sessoes(self):
        ana = make_user('ana')
        self.register(ana, self.talk1)
        self.client.force_login(make_user('bia'))
        response = self.client.get(reverse('registration_sessions', args=[Registration.objects.get(user=ana).pk]))
        self.assertEqual(response.status_code, 404)


class CheckinTests(PerSessionSetup):
    def setUp(self):
        super().setUp()
        self.ana, self.bia = make_user('ana'), make_user('bia')
        self.register(self.ana, self.talk1)
        self.register(self.bia, self.talk2)
        self.client.force_login(self.org)

    def test_so_quem_escolheu_a_sessao(self):
        response = self.client.get(reverse('session_attendance', args=[self.talk1.pk]))
        self.assertContains(response, 'Inscritos nesta sessão')
        self.assertContains(response, 'ana')
        self.assertNotContains(response, 'bia@exemplo.com')
        bia_reg = Registration.objects.get(user=self.bia)
        response = self.client.post(reverse('manual_checkin', args=[self.talk1.pk, bia_reg.pk]))
        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            reverse('qr_checkin', args=[self.talk1.pk]),
            json.dumps({'token': str(bia_reg.qr_token)}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn('não está confirmado nesta sessão', response.json()['message'])
        roster = self.client.get(reverse('checkin_roster', args=[self.talk1.pk])).json()
        self.assertEqual([p['id'] for p in roster['people']], [Registration.objects.get(user=self.ana).pk])

    def test_fila_da_sessao_nao_faz_checkin(self):
        caio = make_user('caio')
        self.register(caio, self.talk1)  # lotada: fila
        self.client.force_login(self.org)
        response = self.client.get(reverse('session_attendance', args=[self.talk1.pk]))
        self.assertContains(response, 'Lista de espera da sessão')
        response = self.client.post(
            reverse('manual_checkin', args=[self.talk1.pk, Registration.objects.get(user=caio).pk])
        )
        self.assertEqual(response.status_code, 404)


class CertificateTests(PerSessionSetup):
    def setUp(self):
        super().setUp()
        self.ana, self.bia = make_user('ana', first_name='Ana', last_name='Lima'), make_user('bia')
        self.register(self.ana, self.talk1, self.talk2)
        self.register(self.bia, self.talk2)
        self.ana_reg = Registration.objects.get(user=self.ana)
        self.bia_reg = Registration.objects.get(user=self.bia)
        Attendance.objects.create(registration=self.ana_reg, session=self.talk1, validated_by=self.org)
        Attendance.objects.create(registration=self.ana_reg, session=self.talk2, validated_by=self.org)
        # Bia não foi à palestra de Redes; tem presença na de IA, mas não estava confirmada nela.
        Attendance.objects.create(registration=self.bia_reg, session=self.talk1, validated_by=self.org)
        mail.outbox.clear()

    def finish(self):
        with self.captureOnCommitCallbacks(execute=True):
            return finish_activity(self.activity)

    def test_um_certificado_por_sessao_com_presenca(self):
        issued = self.finish()
        self.assertEqual(len(issued), 2)
        first = Certificate.objects.get(session_enrollment__session=self.talk1)
        self.assertEqual(first.activity_title, 'Palestra de IA')
        self.assertEqual(first.event_title, 'Semana de TI')
        self.assertEqual(first.workload_hours, 1)
        self.assertEqual(first.start_date, self.talk1.start_at)
        second = Certificate.objects.get(session_enrollment__session=self.talk2)
        self.assertEqual(second.workload_hours, 2)
        self.assertFalse(Certificate.objects.filter(session_enrollment__registration=self.bia_reg).exists())
        # Um e-mail só para Ana, com os dois certificados (Bia recebe só o convite da pesquisa)
        to_ana = [m for m in mail.outbox if m.to == ['ana@exemplo.com']]
        self.assertEqual(len(to_ana), 1)
        self.assertIn('seus certificados de Semana de TI', to_ana[0].subject)
        self.assertIn(first.code, to_ana[0].body)
        self.assertIn(second.code, to_ana[0].body)
        self.assertIn('avaliacao', next(m for m in mail.outbox if m.to == ['bia@exemplo.com']).body)
        # De novo não duplica
        self.assertEqual(issue_again(self.activity), [])

    def test_carga_horaria_informada_na_sessao(self):
        Session.objects.filter(pk=self.talk2.pk).update(workload_hours=4)
        self.finish()
        self.assertEqual(Certificate.objects.get(session_enrollment__session=self.talk2).workload_hours, 4)

    def test_participante_baixa_e_ve_nas_horas(self):
        self.finish()
        code = Certificate.objects.get(session_enrollment__session=self.talk1).code
        self.client.force_login(self.ana)
        response = self.client.get(reverse('certificate_download', args=[code]))
        self.assertEqual(response['Content-Type'], 'application/pdf')
        page = self.client.get(reverse('registration_sessions', args=[self.ana_reg.pk]))
        self.assertContains(page, reverse('certificate_download', args=[code]))
        hours = self.client.get(reverse('my_hours'))
        self.assertContains(hours, 'Palestra de IA (Semana de TI)')
        self.assertContains(self.client.get(reverse('my_registrations')), 'Certificados (2)')
        self.client.force_login(self.bia)
        self.assertEqual(self.client.get(reverse('certificate_download', args=[code])).status_code, 404)

    def test_validacao_publica_mostra_sessao_e_evento(self):
        self.finish()
        certificate = Certificate.objects.get(session_enrollment__session=self.talk1)
        response = self.client.get(reverse('certificate_verify_code', args=[certificate.code]))
        self.assertContains(response, 'Sessão')
        self.assertContains(response, 'Semana de TI')

    def test_texto_do_pdf_cita_sessao_e_evento(self):
        from certificates.pdf import certificate_text
        from core.models import SiteSettings

        self.finish()
        certificate = Certificate.objects.get(session_enrollment__session=self.talk1)
        body = certificate_text(certificate, SiteSettings.load())['body']
        flat = ''.join(text for line in body for text, _ in line)
        self.assertIn('participou da sessão “Palestra de IA”', flat)
        self.assertIn('do evento “Semana de TI”', flat)

    def test_organizacao_revoga_e_ve_na_gestao(self):
        self.finish()
        certificate = Certificate.objects.get(session_enrollment__session=self.talk1)
        self.client.force_login(self.org)
        response = self.client.get(reverse('activity_manage', args=[self.activity.pk]))
        self.assertContains(response, 'Certificado por sessão')
        self.assertContains(response, reverse('certificate_revoke', args=[certificate.code]))
        session = self.client.session
        from accounts.reauth import SESSION_KEY

        session[SESSION_KEY] = timezone.now().timestamp()
        session.save()
        response = self.client.post(reverse('certificate_revoke', args=[certificate.code]), {'reason': 'Engano'})
        self.assertRedirects(response, reverse('activity_manage', args=[self.activity.pk]))
        certificate.refresh_from_db()
        self.assertTrue(certificate.is_revoked)

    def test_modo_nao_muda_depois_dos_certificados(self):
        self.finish()
        self.assertTrue(Certificate.objects.exists())
        from activities.forms import ActivityForm

        self.activity.refresh_from_db()
        data = {
            f: getattr(self.activity, f)
            for f in ActivityForm.Meta.fields
            if f not in ('image', 'per_session', 'start_date', 'end_date', 'registration_start', 'registration_end')
        }
        for f in ('start_date', 'end_date', 'registration_start', 'registration_end'):
            data[f] = local(getattr(self.activity, f))
        data['per_session'] = ''
        form = ActivityForm(data, instance=self.activity)
        self.assertFalse(form.is_valid())
        self.assertIn('per_session', form.errors)

    def test_sessao_com_certificado_nao_e_excluida(self):
        self.finish()
        self.client.force_login(self.org)
        response = self.client.post(reverse('session_delete', args=[self.talk1.pk]), follow=True)
        self.assertContains(response, 'já tem certificados emitidos')
        self.assertTrue(Session.objects.filter(pk=self.talk1.pk).exists())

    def test_csv_mostra_escolhas_e_codigos(self):
        self.finish()
        self.client.force_login(self.org)
        content = self.client.get(reverse('report_registrations_csv', args=[self.activity.pk])).content.decode()
        bia_line = next(line for line in content.splitlines() if 'bia@exemplo.com' in line)
        # Colunas na ordem das sessões. IA: presença sem estar confirmada (P); Oficina: não escolheu (-);
        # Redes: escolheu e faltou (F)
        self.assertIn(';P;-;F;', bia_line.replace('"', ''))
        code = Certificate.objects.get(session_enrollment__session=self.talk1).code
        self.assertIn(code, content)


def issue_again(activity):
    from certificates.services import issue_certificates

    return issue_certificates(activity)


class OrganizerTests(PerSessionSetup):
    def test_sessao_exige_vagas_ate_o_limite_do_evento(self):
        from activities.forms import SessionForm

        start = self.activity.start_date
        data = {'title': 'Nova', 'start_at': local(start), 'end_at': local(start + timedelta(hours=1))}
        form = SessionForm(data, activity=self.activity)
        self.assertFalse(form.is_valid())
        self.assertIn('capacity', form.errors)
        form = SessionForm({**data, 'capacity': 11}, activity=self.activity)
        self.assertFalse(form.is_valid())
        self.assertIn('Não pode passar das vagas da atividade', str(form.errors))
        self.assertTrue(SessionForm({**data, 'capacity': 3}, activity=self.activity).is_valid())

    def test_sem_o_modo_a_sessao_nao_pede_vagas(self):
        from activities.forms import SessionForm

        other = make_activity(self.org, title='Curso')
        self.assertNotIn('capacity', SessionForm(activity=other).fields)

    def test_aumentar_vagas_chama_a_fila_da_sessao(self):
        ana, bia = make_user('ana'), make_user('bia')
        self.register(ana, self.talk1)
        self.register(bia, self.talk1)
        self.client.force_login(self.org)
        start = self.talk1.start_at
        data = {
            'title': self.talk1.title,
            'start_at': local(start),
            'end_at': local(self.talk1.end_at),
            'capacity': 2,
            'check_in_open': 'on',
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('session_edit', args=[self.talk1.pk]), data, follow=True)
        self.assertContains(response, 'foram confirmadas')
        self.assertEqual(self.enrollment(bia, self.talk1).status, S.CONFIRMED)

    def test_nao_reduz_abaixo_dos_confirmados(self):
        from activities.forms import SessionForm

        self.register(make_user('ana'), self.talk2)
        self.register(make_user('bia'), self.talk2)
        data = {
            'title': self.talk2.title,
            'start_at': local(self.talk2.start_at),
            'end_at': local(self.talk2.end_at),
            'capacity': 1,
        }
        form = SessionForm(data, instance=self.talk2)
        self.assertFalse(form.is_valid())
        self.assertIn('Já há 2 participantes confirmados', str(form.errors))

    def test_excluir_sessao_avisa_quem_escolheu(self):
        ana = make_user('ana')
        self.register(ana, self.talk2)
        mail.outbox.clear()
        self.client.force_login(self.org)
        self.assertContains(
            self.client.get(reverse('session_delete', args=[self.talk2.pk])), '1 participante(s) escolheram'
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('session_delete', args=[self.talk2.pk]))
        self.assertEqual(mail.outbox[0].to, ['ana@exemplo.com'])
        self.assertIn('sessão cancelada', mail.outbox[0].subject)

    def test_ligar_o_modo_preenche_as_vagas_das_sessoes(self):
        from activities.forms import ActivityForm

        other = make_activity(self.org, title='Congresso', capacity=30)
        make_session(other)
        data = {
            f: getattr(other, f)
            for f in ActivityForm.Meta.fields
            if f not in ('image', 'start_date', 'end_date', 'registration_start', 'registration_end')
        }
        for f in ('start_date', 'end_date', 'registration_start', 'registration_end'):
            data[f] = local(getattr(other, f))
        data['per_session'] = 'on'
        form = ActivityForm(data, instance=other)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(form.sessions_filled, 1)
        self.assertEqual(other.sessions.get().capacity, 30)

    def test_exclusao_de_conta_libera_as_sessoes(self):
        from accounts.privacy import anonymize, export_data

        ana, bia = make_user('ana'), make_user('bia')
        self.register(ana, self.talk1)
        self.register(bia, self.talk1)
        exported = export_data(ana)
        self.assertEqual(exported['inscricoes'][0]['sessoes_escolhidas'][0]['sessao'], 'Palestra de IA')
        with self.captureOnCommitCallbacks(execute=True):
            anonymize(ana)
        self.assertEqual(self.enrollment(ana, self.talk1).status, S.CANCELLED)
        self.assertEqual(self.enrollment(bia, self.talk1).status, S.CONFIRMED)


class SessionP2Tests(PerSessionSetup):
    def edit_session(self, session, **changes):
        data = {
            'title': session.title,
            'start_at': local(session.start_at),
            'end_at': local(session.end_at),
            'location': session.location,
            'capacity': session.capacity,
            'notify_change': 'on',
            **changes,
        }
        self.client.force_login(self.org)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse('session_edit', args=[session.pk]), data, follow=True)

    def test_mudanca_de_horario_avisa_e_mostra_conflitos(self):
        ana = make_user('ana')
        self.register(ana, self.talk1, self.talk2)
        SessionEnrollment.objects.filter(session=self.talk1).update(reminder_sent_at=timezone.now())
        mail.outbox.clear()
        # A palestra de Redes passa a acontecer junto com a de IA
        response = self.edit_session(self.talk2, start_at=local(self.talk1.start_at), end_at=local(self.talk1.end_at))
        self.assertContains(response, '1 pessoa(s) avisada(s)')
        self.assertContains(response, 'outra sessão escolhida no mesmo horário')
        self.assertEqual(mail.outbox[0].to, ['ana@exemplo.com'])
        self.assertIn('mudança na sessão Palestra de Redes', mail.outbox[0].subject)
        self.assertIn('antes:', mail.outbox[0].body)
        self.assertIsNotNone(SessionEnrollment.objects.get(session=self.talk1).reminder_sent_at)

    def test_sem_mudanca_de_horario_nao_avisa(self):
        self.register(make_user('ana'), self.talk2)
        mail.outbox.clear()
        self.edit_session(self.talk2, title='Redes (novo título)')
        self.assertEqual(mail.outbox, [])

    def test_aviso_para_quem_nao_escolheu_sessao(self):
        ana, bia = make_user('ana'), make_user('bia')
        self.register(ana, self.talk2)
        Registration.objects.create(user=bia, activity=self.activity)  # inscrita antes de ligar a opção
        self.client.force_login(self.org)
        page = self.client.get(reverse('activity_manage', args=[self.activity.pk]))
        self.assertContains(page, '1 inscrito ainda sem nenhuma sessão')
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('sessions_remind', args=[self.activity.pk]))
        self.assertEqual([m.to for m in mail.outbox], [['bia@exemplo.com']])
        self.assertIn('escolha as suas sessões', mail.outbox[0].subject)

    def test_lembrete_por_sessao(self):
        from io import StringIO

        from django.core.management import call_command

        ana = make_user('ana')
        self.register(ana, self.talk1, self.talk2)
        Session.objects.filter(pk=self.talk1.pk).update(
            start_at=timezone.now() + timedelta(hours=2), end_at=timezone.now() + timedelta(hours=3)
        )
        mail.outbox.clear()
        call_command('send_reminders', stdout=StringIO())
        to_ana = [m for m in mail.outbox if m.to == ['ana@exemplo.com']]
        self.assertEqual(len(to_ana), 1)  # um e-mail só, sem o lembrete geral do evento
        self.assertIn('Palestra de IA', to_ana[0].body)
        self.assertNotIn('Palestra de Redes', to_ana[0].body)  # fora da janela de 24 horas
        mail.outbox.clear()
        call_command('send_reminders', stdout=StringIO())
        self.assertEqual(mail.outbox, [])

    def test_relatorios_usam_as_sessoes_escolhidas(self):
        from activities.operations import activity_numbers

        ana = make_user('ana')
        self.register(ana, self.talk2)
        registration = Registration.objects.get(user=ana)
        Attendance.objects.create(registration=registration, session=self.talk2, validated_by=self.org)
        self.assertEqual(activity_numbers(self.activity)['avg_frequency'], 100)
        self.client.force_login(self.org)
        response = self.client.get(reverse('report_attendance_pdf', args=[self.activity.pk]))
        self.assertEqual(response['Content-Type'], 'application/pdf')
