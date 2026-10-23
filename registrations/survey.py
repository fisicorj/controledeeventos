"""Pesquisa de satisfação depois da atividade: nota de 0 a 10 e comentário."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render

from activities.models import Activity
from core.csvsafe import excel_writer

from .models import Registration, SessionRating, SurveyResponse


class SurveyForm(forms.ModelForm):
    score = forms.TypedChoiceField(
        label='De 0 a 10, quanto você recomendaria esta atividade a um colega?',
        choices=[(n, str(n)) for n in range(11)],
        coerce=int,
        widget=forms.RadioSelect,
        error_messages={'required': 'Escolha uma nota de 0 a 10.'},
    )

    class Meta:
        model = SurveyResponse
        fields = ['score', 'comment']
        labels = {'comment': 'Quer comentar algo? O que foi bom e o que podemos melhorar (opcional)'}
        widgets = {'comment': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, sessions=(), **kwargs):
        super().__init__(*args, **kwargs)
        # Modo por sessão (M-08): uma nota opcional para cada sessão em que a pessoa esteve.
        self.sessions = list(sessions)
        for session in self.sessions:
            self.fields[f's{session.pk}'] = forms.TypedChoiceField(
                label=f'Nota para "{session.title}"',
                choices=[('', 'Sem nota')] + [(n, str(n)) for n in range(11)],
                coerce=int,
                empty_value=None,
                required=False,
            )
            self.fields[f'c{session.pk}'] = forms.CharField(
                label=f'Comentário sobre "{session.title}"',
                required=False,
                max_length=1000,
                widget=forms.Textarea(attrs={'rows': 2}),
            )

    def session_fields(self):
        return [(session, self[f's{session.pk}'], self[f'c{session.pk}']) for session in self.sessions]

    def save_ratings(self, response):
        for session in self.sessions:
            score = self.cleaned_data.get(f's{session.pk}')
            if score is not None:
                SessionRating.objects.create(
                    response=response,
                    session=session,
                    score=score,
                    comment=(self.cleaned_data.get(f'c{session.pk}') or '').strip(),
                )


def eligible(activity):
    """Quem pode responder: confirmados que participaram (com presença, quando há sessões)."""
    qs = activity.registrations.filter(status=Registration.Status.CONFIRMED)
    if activity.sessions.exists():
        qs = qs.annotate(n=Count('attendances')).filter(n__gt=0)
    return qs


def attended_sessions(registration):
    """Sessões com presença da pessoa, para a nota por sessão (só no modo por sessão)."""
    if not registration.activity.per_session:
        return []
    from activities.models import Session

    return list(Session.objects.filter(attendances__registration=registration).distinct().order_by('start_at'))


def session_summary(session):
    """Notas de uma sessão (M-08): quantas, média e comentários (ordenados pela nota, sem identificar ninguém)."""
    ratings = list(SessionRating.objects.filter(session=session))
    if not ratings:
        return {'session': session, 'count': 0}
    return {
        'session': session,
        'count': len(ratings),
        'average': round(sum(r.score for r in ratings) / len(ratings), 1),
        'comments': sorted((r for r in ratings if r.comment.strip()), key=lambda r: (r.score, r.comment.lower())),
    }


def survey_open(activity):
    return activity.survey_enabled and activity.status == Activity.Status.FINISHED


def needs_survey(registration):
    """Certificado bloqueado até responder a pesquisa?"""
    activity = registration.activity
    return (
        survey_open(activity)
        and activity.survey_required
        and not SurveyResponse.objects.filter(registration=registration).exists()
    )


@login_required
def survey(request, pk):
    registration = get_object_or_404(
        Registration.objects.select_related('activity'), pk=pk, user=request.user, status=Registration.Status.CONFIRMED
    )
    activity = registration.activity
    if not survey_open(activity) or not eligible(activity).filter(pk=registration.pk).exists():
        messages.info(request, 'A pesquisa desta atividade não está disponível para você.')
        return redirect('my_registrations')
    existing = SurveyResponse.objects.filter(registration=registration).first()
    if existing:
        messages.info(request, 'Você já respondeu a pesquisa desta atividade. Obrigado!')
        return redirect('my_registrations')
    form = SurveyForm(request.POST if request.method == 'POST' else None, sessions=attended_sessions(registration))
    if form.is_valid():
        response = form.save(commit=False)
        response.registration = registration
        try:
            with transaction.atomic():
                response.save()
                form.save_ratings(response)
        except IntegrityError:  # duplo clique: a primeira resposta já foi gravada
            messages.info(request, 'Você já respondeu a pesquisa desta atividade. Obrigado!')
            return redirect('my_registrations')
        text = 'Obrigado pela avaliação!'
        if activity.survey_required and hasattr(registration, 'certificate'):
            text += ' Seu certificado já está liberado.'
        messages.success(request, text)
        return redirect('my_registrations')
    return render(request, 'registrations/survey.html', {'form': form, 'activity': activity})


def summary(activity):
    """Números da pesquisa para a página de gestão."""
    responses = list(SurveyResponse.objects.filter(registration__activity=activity).order_by('-created_at'))
    total = len(responses)
    invited = eligible(activity).count()
    sessions = [session_summary(s) for s in activity.sessions.order_by('start_at')] if activity.per_session else []
    sessions = [s for s in sessions if s['count']]
    if not total:
        return {'total': 0, 'invited': invited, 'pending': invited, 'sessions': sessions}
    scores = [r.score for r in responses]
    promoters = sum(1 for s in scores if s >= 9)
    detractors = sum(1 for s in scores if s <= 6)
    distribution = [sum(1 for s in scores if s == n) for n in range(11)]
    peak = max(distribution) or 1
    return {
        'total': total,
        'invited': invited,
        'rate': round(total * 100 / invited) if invited else None,
        'pending': max(invited - total, 0),
        'average': round(sum(scores) / total, 1),
        'nps': round((promoters - detractors) * 100 / total),
        'promoters': promoters,
        'passives': total - promoters - detractors,
        'detractors': detractors,
        'bars': [{'score': n, 'count': c, 'height': round(c * 100 / peak)} for n, c in enumerate(distribution)],
        # Ordenados pela nota (e não pela hora da resposta): em turma pequena, a hora identificaria quem respondeu.
        'comments': sorted((r for r in responses if r.comment.strip()), key=lambda r: (r.score, r.comment.lower())),
        'sessions': sessions,
    }


@login_required
def survey_csv(request, activity_id):
    """Respostas sem identificação de quem respondeu: só o dia (sem a hora) e em ordem de nota, não de chegada."""

    from django.http import HttpResponse
    from django.utils import timezone

    from core.permissions import can_manage

    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{activity.slug}-avaliacoes.csv"'
    response.write('﻿')
    writer = excel_writer(response)
    writer.writerow(['Data', 'Nota', 'Comentário'])
    for r in SurveyResponse.objects.filter(registration__activity=activity).order_by('score', 'comment'):
        writer.writerow([timezone.localtime(r.created_at).strftime('%d/%m/%Y'), r.score, r.comment])
    ratings = SessionRating.objects.filter(session__activity=activity).select_related('session')
    if ratings.exists():
        writer.writerow([])
        writer.writerow(['Sessão', 'Nota', 'Comentário'])
        for r in ratings.order_by('session__start_at', 'score', 'comment'):
            writer.writerow([r.session.title, r.score, r.comment])
    return response


@login_required
def survey_remind(request, activity_id):
    """Reenvia o convite para quem ainda não respondeu."""

    from core.permissions import can_manage

    from . import notifications

    activity = get_object_or_404(Activity, pk=activity_id)
    if not can_manage(request.user, activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method == 'POST' and survey_open(activity):
        pending = list(eligible(activity).filter(survey__isnull=True).select_related('user', 'activity'))
        notifications.notify_survey(pending)
        messages.success(request, f'Convite reenviado para {len(pending)} pessoa{"s" if len(pending) != 1 else ""}.')
    return redirect('activity_manage', pk=activity.pk)
