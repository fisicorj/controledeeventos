"""Direitos do titular (LGPD): cópia dos dados pessoais e exclusão da conta.

A exclusão anonimiza a conta em vez de apagar as linhas do banco: presenças, certificados emitidos e a auditoria
continuam valendo (a instituição precisa guardar o registro do que certificou), mas sem nome, e-mail, matrícula
ou CPF ligados à conta. Respostas do formulário de inscrição são apagadas; inscrições futuras são canceladas e a
vaga passa para a lista de espera.
"""

import secrets

from django.db import transaction
from django.utils import timezone

from .models import DELETED_PREFIX, User


def can_delete_self(user):
    """Só participantes excluem a própria conta. Organizador e administrador assinam e respondem por atividades:
    a exclusão deles passa por outro administrador."""
    return user.role == User.Role.PARTICIPANT and not user.is_superuser and not user.is_staff


def _when(value):
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M') if value else None


def export_data(user):
    """Tudo o que o portal guarda sobre a pessoa, num dicionário pronto para virar JSON."""
    from certificates.history import ownership
    from certificates.models import Certificate
    from registrations.models import Registration, SurveyResponse

    registrations = (
        Registration.objects.filter(user=user)
        .select_related('activity', 'category')
        .prefetch_related('answers__question', 'attendances__session', 'session_enrollments__session')
        .order_by('created_at')
    )
    session_certificates = {
        c.session_enrollment_id: c for c in Certificate.objects.filter(session_enrollment__registration__user=user)
    }
    certificates = {c.registration_id: c for c in Certificate.objects.filter(registration__user=user)}
    data = {
        'gerado_em': _when(timezone.now()),
        'conta': {
            'usuario': user.username,
            'nome': user.first_name,
            'sobrenome': user.last_name,
            'email': user.email,
            'email_confirmado': user.email_verified,
            'matricula_ou_cpf': user.institutional_id,
            'criada_em': _when(user.date_joined),
            'ultimo_acesso': _when(user.last_login),
            'aceite_do_aviso_de_privacidade': _when(user.privacy_accepted_at),
        },
        'inscricoes': [],
        'avaliacoes': [
            {
                'atividade': s.registration.activity.title,
                'nota': s.score,
                'comentario': s.comment,
                'data': _when(s.created_at),
            }
            for s in SurveyResponse.objects.filter(registration__user=user).select_related('registration__activity')
        ],  # fmt: skip
        'certificados_avulsos': [
            {
                'evento': c.activity_title,
                'nome_no_certificado': c.participant_name,
                'codigo': c.code,
                'revogado': c.is_revoked,
            }
            # Mesma regra de "Minhas horas": pelo e-mail, pela matrícula confirmada ou vinculados pelo código.
            for c in Certificate.objects.filter(ownership(user), batch__isnull=False).distinct().order_by('pk')
        ],
        'equipe_de_checkin': list(user.checkin_activities.values_list('title', flat=True)),
        'trabalhos_enviados': [
            {
                'evento': sub.call.activity.title,
                'titulo': sub.title,
                'coautores': sub.coauthor_list(),
                'situacao': sub.get_status_display(),
                'enviado_em': _when(sub.created_at),
                'arquivo': sub.file_name,
                'certificado_de_apresentacao': getattr(getattr(sub, 'certificate', None), 'code', None),
            }
            for sub in user.submissions.select_related('call__activity')
        ],
        'avaliacoes_de_trabalhos': [
            {
                'evento': r.submission.call.activity.title,
                'trabalho': r.submission.title,
                'entregue_em': _when(r.submitted_at),
            }
            for r in user.reviews.select_related('submission__call__activity')
        ],
        'declaracoes_de_horas': [
            {
                'codigo': d.code,
                'emitida_em': _when(d.created_at),
                'semestre': d.semester or 'todos',
                'horas': d.total_hours,
            }
            for d in user.hours_declarations.all()
        ],
    }
    for r in registrations:
        certificate = certificates.get(r.pk)
        data['inscricoes'].append(
            {
                'atividade': r.activity.title,
                'inicio_da_atividade': _when(r.activity.start_date),
                'situacao': r.get_status_display(),
                'tipo_de_vaga': r.category.name if r.category_id else None,
                'inscrito_em': _when(r.created_at),
                'cancelada_em': _when(r.cancelled_at),
                'respostas': {a.question.label: a.value for a in r.answers.all()},
                'presencas': [
                    {'sessao': a.session.title, 'registrada_em': _when(a.check_in)} for a in r.attendances.all()
                ],
                'certificado': {
                    'codigo': certificate.code,
                    'nome_no_certificado': certificate.participant_name,
                    'revogado': certificate.is_revoked,
                }
                if certificate
                else None,
            }
        )
        if r.activity.per_session:
            data['inscricoes'][-1]['sessoes_escolhidas'] = [
                _session_choice(e, session_certificates.get(e.pk)) for e in r.session_enrollments.all()
            ]
    return data


def _session_choice(enrollment, certificate):
    return {
        'sessao': enrollment.session.title,
        'inicio': _when(enrollment.session.start_at),
        'situacao': enrollment.get_status_display(),
        'escolhida_em': _when(enrollment.created_at),
        'cancelada_em': _when(enrollment.cancelled_at),
        'certificado': {
            'codigo': certificate.code,
            'nome_no_certificado': certificate.participant_name,
            'revogado': certificate.is_revoked,
        }
        if certificate
        else None,
    }


def _leave_submissions(user):
    """Trabalhos ainda em avaliação são retirados (os aprovados continuam no evento, com os nomes que foram
    publicados); o arquivo dos não aprovados é apagado. Sai da lista de avaliadores e das avaliações pendentes."""
    from submissions.models import Submission

    pending = user.submissions.exclude(status__in=Submission.FINAL)
    for submission in pending:
        submission.reviews.filter(submitted_at__isnull=True).delete()
    pending.update(status=Submission.Status.WITHDRAWN)
    for submission in user.submissions.exclude(status=Submission.Status.ACCEPTED):
        if submission.file:
            submission.file.delete(save=False)
        submission.file_name = ''
        submission.save(update_fields=['file', 'file_name'])
    user.reviews.filter(submitted_at__isnull=True).delete()
    user.review_calls.clear()


def keep_signature(user, only_finished=False):
    """Quando a instituição não tem signatário próprio, os certificados saem com o nome de quem organizou ou
    emitiu. Antes de esse nome sumir (exclusão) ou de a atividade mudar de dono (transferência), ele fica gravado
    na atividade e no lote, e os certificados já emitidos continuam iguais.

    only_finished: na transferência, quem recebe pode passar a assinar as atividades que ainda não terminaram
    (os certificados delas ainda não saíram). As finalizadas e as emissões avulsas sempre mantêm o nome."""
    from activities.models import Activity
    from certificates.models import CertificateBatch

    name = user.get_full_name() or user.username
    activities = Activity.objects.filter(organizer=user, signer_name='')
    if only_finished:
        activities = activities.filter(status=Activity.Status.FINISHED)
    activities.update(signer_name=name)
    CertificateBatch.objects.filter(created_by=user, signer_name='').update(signer_name=name)


@transaction.atomic
def anonymize(user):
    """Exclui a conta: tira os dados pessoais e impede o acesso. Devolve quantas inscrições futuras foram canceladas."""
    from core import outbox
    from registrations import services, sessions
    from registrations.models import Registration, RegistrationAnswer

    now = timezone.now()
    cancelled = 0
    upcoming = Registration.objects.filter(
        user=user,
        status__in=[Registration.Status.CONFIRMED, Registration.Status.WAITLISTED],
        activity__start_date__gt=now,
    ).values_list('pk', 'activity_id')
    for registration_id, activity_id in upcoming:
        activity = services.lock_activity(activity_id)
        registration = Registration.objects.get(pk=registration_id)
        was_confirmed = registration.status == Registration.Status.CONFIRMED
        registration.status, registration.waitlisted_at, registration.cancelled_at = (
            Registration.Status.CANCELLED, None, now
        )  # fmt: skip
        registration.save(update_fields=['status', 'waitlisted_at', 'cancelled_at'])
        cancelled += 1
        sessions.cancel_all(registration)
        if was_confirmed:
            services.promote_waitlist(activity)  # a vaga vai para o próximo da fila
    # Atividade por sessão já em andamento: a inscrição fica, mas as sessões que ainda vão acontecer são liberadas.
    ongoing = Registration.objects.filter(
        user=user, status=Registration.Status.CONFIRMED, activity__per_session=True
    ).values_list('pk', 'activity_id')
    for registration_id, activity_id in ongoing:
        services.lock_activity(activity_id)
        sessions.cancel_all(Registration.objects.get(pk=registration_id))
    _leave_submissions(user)
    RegistrationAnswer.objects.filter(registration__user=user).delete()
    outbox.forget_address(user.email)  # o que não saiu é descartado; do que saiu, some destinatário e conteúdo
    user.email_codes.all().delete()
    user.checkin_activities.clear()
    user.claimed_certificates.update(claimed_by=None, claimed_at=None)  # certificados avulsos vinculados pelo código
    # Sufixo aleatório: o nome não pode ser previsto nem reservado por outra pessoa (SEC-21 da 3ª auditoria).
    keep_signature(user)
    user.hours_declarations.all().delete()  # a página pública da declaração mostra o nome: sai junto
    tag = f'{DELETED_PREFIX}-{user.pk}-{secrets.token_hex(4)}'
    user.username = tag
    user.first_name, user.last_name = 'Conta', 'excluída'
    user.email = f'{tag}@conta-excluida.invalid'
    user.institutional_id = ''
    user.email_verified = False
    user.is_active = False
    user.set_unusable_password()
    user.deleted_at = now
    user.save()
    return cancelled
