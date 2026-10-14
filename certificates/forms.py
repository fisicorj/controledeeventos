from django import forms
from django.utils import timezone

from core.text import single_line

from .models import CertificateBatch


class DateInput(forms.DateInput):
    input_type = 'date'


class BatchForm(forms.ModelForm):
    class Meta:
        model = CertificateBatch
        fields = ['title', 'activity_type', 'start_date', 'end_date', 'workload_hours', 'issue_date']
        widgets = {k: DateInput(format='%Y-%m-%d') for k in ('start_date', 'end_date', 'issue_date')}
        help_texts = {
            'title': 'Sai em negrito no certificado, entre aspas, exatamente como digitado aqui.',
            'end_date': 'Para evento de um dia só, repita a data de início.',
            'workload_hours': 'Deixe em branco para não citar a duração no certificado.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['workload_hours'].widget.attrs.update(min=1, max=2000)

    def clean_title(self):
        return single_line(self.cleaned_data.get('title'))

    def clean_workload_hours(self):
        hours = self.cleaned_data.get('workload_hours')
        if hours is not None and not 1 <= hours <= 2000:
            raise forms.ValidationError('Informe entre 1 e 2000 horas.')
        return hours

    def clean(self):
        data = super().clean()
        start, end = data.get('start_date'), data.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'A data de término não pode ser anterior à de início.')
        issue = data.get('issue_date')
        if issue and issue > timezone.localdate():
            self.add_error('issue_date', 'A data do certificado não pode ser no futuro.')
        elif issue and start and issue < start:
            self.add_error('issue_date', 'A data do certificado não pode ser anterior ao início do evento.')
        return data


class SpreadsheetForm(forms.Form):
    spreadsheet = forms.FileField(
        label='Planilha de participantes',
        help_text='Arquivo .xlsx ou .csv com o cabeçalho na primeira linha: Nome, Matrícula, Turma e, '
        'se quiser enviar por e-mail, E-mail.',
        widget=forms.ClearableFileInput(attrs={'accept': '.xlsx,.csv'}),
    )
    fix_case = forms.BooleanField(
        label='Ajustar maiúsculas e minúsculas dos nomes',
        required=False,
        initial=True,
        help_text='"joaninha melo rego" vira "Joaninha Melo Rego". Você confere o antes e o depois na prévia.',
    )


class FullSpreadsheetForm(SpreadsheetForm):
    """Planilha completa: os dados do evento vêm nas colunas, junto com os participantes."""

    spreadsheet = forms.FileField(
        label='Planilha completa',
        help_text='Arquivo .xlsx ou .csv com as colunas do evento (Evento, Início...) e dos participantes (Nome...). '
        'Um evento por planilha.',
        widget=forms.ClearableFileInput(attrs={'accept': '.xlsx,.csv'}),
    )


class ReasonForm(forms.Form):
    reason = forms.CharField(
        label='Motivo',
        min_length=5,
        max_length=500,
        error_messages={
            'required': 'Informe o motivo.',
            'min_length': 'Explique o motivo com pelo menos 5 caracteres.',
        },
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Fica registrado na auditoria e aparece para quem validar o código. '
        'Ex.: "Nome digitado errado, reemitido com o código ABCD-EFGH-JKMN".',
    )


class CorrectionForm(forms.Form):
    """Correção do nome ou da matrícula num certificado já emitido (o código continua o mesmo)."""

    participant_name = forms.CharField(label='Nome no certificado', max_length=300)
    participant_document = forms.CharField(label='Matrícula ou CPF', max_length=50, required=False)
    reason = forms.CharField(
        label='Motivo da correção',
        min_length=5,
        max_length=500,
        error_messages={'min_length': 'Explique o motivo com pelo menos 5 caracteres.'},
        widget=forms.Textarea(attrs={'rows': 2}),
        help_text='Fica registrado na auditoria, com o antes e o depois.',
    )

    def clean_participant_name(self):
        return ' '.join(self.cleaned_data['participant_name'].split())

    def clean_participant_document(self):
        return ''.join(self.cleaned_data['participant_document'].split())
