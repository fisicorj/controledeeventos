from django import forms
from django.core.files.uploadedfile import UploadedFile

from activities.forms import DateTimeLocalInput
from core.text import single_line

from .models import CallForPapers, Review, ReviewDecline, Submission

FILE_EXTENSIONS = {'pdf', 'doc', 'docx', 'odt'}
MAX_FILE_MB = 10


class CallForm(forms.ModelForm):
    class Meta:
        model = CallForPapers
        fields = [
            'opens_at',
            'closes_at',
            'instructions',
            'areas',
            'blind_review',
            'max_authors',
            'max_submissions',
            'review_days',
            'criteria',
        ]
        widgets = {
            'criteria': forms.Textarea(attrs={'rows': 4}),
            'opens_at': DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
            'closes_at': DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
            'instructions': forms.Textarea(attrs={'rows': 8}),
            'areas': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, activity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if activity is not None:
            self.instance.activity = activity
        self.activity = self.instance.activity
        self.fields['review_days'].required = False  # em branco: 14 dias
        if self.instance.pk and self.instance.submissions.filter(reviews__isnull=False).exists():
            # Com avaliadores já designados, trocar o modo exporia (ou esconderia no meio) o nome dos autores.
            self.fields['blind_review'].disabled = True
            self.fields['blind_review'].help_text = 'Não muda mais: já há avaliadores designados.'
        if (
            self.instance.pk
            and Review.objects.filter(submission__call=self.instance, submitted_at__isnull=False).exists()
        ):
            # As notas entregues são dos critérios de hoje: trocar os critérios deixaria notas sem critério.
            self.fields['criteria'].disabled = True
            self.fields['criteria'].help_text = 'Não muda mais: já há avaliações entregues com estes critérios.'

    def clean_review_days(self):
        return self.cleaned_data.get('review_days') or 14

    def clean(self):
        data = super().clean()
        opens, closes = data.get('opens_at'), data.get('closes_at')
        if opens and closes and closes <= opens:
            self.add_error('closes_at', 'O fim das submissões deve ser depois do início.')
        if closes and closes > self.activity.end_date:
            self.add_error('closes_at', 'As submissões devem encerrar até o término do evento.')
        return data


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ['title', 'area', 'abstract', 'keywords', 'coauthors', 'file']
        widgets = {
            'abstract': forms.Textarea(attrs={'rows': 8}),
            'coauthors': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, call=None, author_email='', **kwargs):
        super().__init__(*args, **kwargs)
        self.call = call or self.instance.call
        self.author_email = author_email
        self.metadata_removed = False
        areas = self.call.area_list()
        if areas:
            self.fields['area'] = forms.ChoiceField(
                label='Área temática', choices=[('', 'Selecione...')] + [(a, a) for a in areas]
            )
        else:
            del self.fields['area']
        self.fields['file'].help_text = f'PDF, DOC, DOCX ou ODT, até {MAX_FILE_MB} MB.' + (
            ' A avaliação é às cegas: tire do texto os nomes dos autores e da instituição (os metadados do PDF, DOCX '
            'e ODT, como o campo "autor", são apagados ao enviar; no DOC não).'
            if self.call.blind_review
            else ''
        )
        self.fields['file'].widget.attrs['accept'] = '.pdf,.doc,.docx,.odt'
        if self.call.max_authors == 1:
            del self.fields['coauthors']

    def clean_title(self):
        return single_line(self.cleaned_data.get('title'))

    def clean_coauthors(self):
        names = [line.strip() for line in (self.cleaned_data.get('coauthors') or '').splitlines() if line.strip()]
        if len(names) + 1 > self.call.max_authors:
            raise forms.ValidationError(
                f'No máximo {self.call.max_authors} autores por trabalho, contando você ({len(names) + 1} informados).'
            )
        if any(len(name) > 250 for name in names):
            raise forms.ValidationError('Cada coautor numa linha, com até 250 caracteres.')
        from django.core.validators import validate_email

        from .models import EMAIL_IN_LINE, parse_coauthor

        seen = set()
        own = (self.instance.author.email if self.instance.author_id else self.author_email or '').lower()
        for line in names:
            if len(EMAIL_IN_LINE.findall(line)) > 1:
                raise forms.ValidationError(f'Coloque um e-mail só por coautor: "{line}".')
            name, email = parse_coauthor(line)
            if not name:
                raise forms.ValidationError(f'Falta o nome do coautor em "{line}".')
            if email:
                try:
                    validate_email(email)
                except forms.ValidationError:
                    raise forms.ValidationError(f'E-mail inválido em "{line}".') from None
                if email == own:
                    raise forms.ValidationError('O seu próprio e-mail não entra entre os coautores.')
                if email in seen:
                    raise forms.ValidationError(f'O e-mail {email} aparece em mais de um coautor.')
                seen.add(email)
        return '\n'.join(names)

    def clean_file(self):
        file = self.cleaned_data.get('file')
        if isinstance(file, UploadedFile):  # arquivo novo (na edição sem troca, vem o atual)
            if file.size > MAX_FILE_MB * 1024 * 1024:
                raise forms.ValidationError(f'O arquivo deve ter no máximo {MAX_FILE_MB} MB.')
            extension = file.name.rsplit('.', 1)[-1].lower() if '.' in file.name else ''
            if extension not in FILE_EXTENSIONS:
                raise forms.ValidationError('Envie o trabalho em PDF, DOC, DOCX ou ODT.')
            from .metadata import Rejected, check_archive, strip

            try:
                check_archive(file)  # DOCX e ODT que descompactam em gigabytes não entram (em nenhuma chamada)
                if self.call.blind_review:
                    file, self.metadata_removed = strip(file)
            except Rejected as exc:
                raise forms.ValidationError(str(exc)) from exc
        return file


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['recommendation', 'score', 'comments_to_author', 'comments_to_organizer']
        widgets = {
            'recommendation': forms.RadioSelect,
            'comments_to_author': forms.Textarea(attrs={'rows': 8}),
            'comments_to_organizer': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, criteria=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recommendation'].required = True
        self.fields['recommendation'].choices = Review.Recommendation.choices
        self.fields['score'].required = True
        self.fields['comments_to_author'].required = True
        # Critérios da chamada (R6): uma nota de 1 a 5 por critério, e a nota do trabalho é a média deles.
        self.criteria = list(criteria)
        if self.criteria:
            del self.fields['score']
            saved = self.instance.criteria_scores or {}
            for index, name in enumerate(self.criteria):
                self.fields[f'criterion_{index}'] = forms.TypedChoiceField(
                    label=name,
                    choices=[(n, str(n)) for n in range(1, 6)],
                    coerce=int,
                    widget=forms.RadioSelect(attrs={'class': 'criterion-scale'}),
                    initial=saved.get(name),
                    help_text='1 = fraco, 5 = excelente.',
                    error_messages={'required': f'Dê uma nota de 1 a 5 para "{name}".'},
                )

    def save(self, commit=True):
        review = super().save(commit=False)
        if self.criteria:
            scores = {name: self.cleaned_data[f'criterion_{i}'] for i, name in enumerate(self.criteria)}
            review.criteria_scores = scores
            review.score = int(sum(scores.values()) / len(scores) + 0.5)  # média arredondada (2,5 vira 3)
        if commit:
            review.save()
        return review


class DeclineForm(forms.Form):
    reason = forms.ChoiceField(
        label='Por que você não vai avaliar?', choices=ReviewDecline.Reason.choices, widget=forms.RadioSelect
    )
    note = forms.CharField(
        label='Observação para a organização (opcional)',
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={'rows': 2}),
    )


class DecisionForm(forms.Form):
    DECISIONS = [
        (Submission.Status.ACCEPTED, 'Aprovar (o autor fica inscrito no evento)'),
        (Submission.Status.REVISION, 'Pedir correções ao autor'),
        (Submission.Status.REJECTED, 'Não aprovar'),
    ]
    decision = forms.ChoiceField(label='Decisão', choices=DECISIONS, widget=forms.RadioSelect)
    note = forms.CharField(
        label='Mensagem ao autor',
        required=False,
        max_length=4000,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Vai no e-mail, junto com os comentários dos avaliadores (sem os nomes deles).',
    )
    # Aprovar inscreve o autor direto, sem tipo de vaga, código de acesso nem perguntas da inscrição, e mesmo com o
    # evento lotado; na sessão do trabalho ele fica confirmado acima das vagas (quem apresenta tem lugar).

    def clean(self):
        data = super().clean()
        if data.get('decision') == Submission.Status.REVISION and not (data.get('note') or '').strip():
            self.add_error('note', 'Explique ao autor o que precisa ser corrigido.')
        return data


class ReopenForm(forms.Form):
    note = forms.CharField(
        label='Motivo',
        max_length=2000,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Fica no histórico do trabalho e vai no e-mail ao autor.',
    )
