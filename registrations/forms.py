from django import forms

from activities.models import RegistrationQuestion

from .models import RegistrationAnswer

Kind = RegistrationQuestion.Kind


class RegistrationAnswersForm(forms.Form):
    """Formulário montado a partir das perguntas que o organizador cadastrou na atividade."""

    def __init__(self, questions, *args, initial_answers=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.questions = list(questions)
        initial_answers = initial_answers or {}
        for question in self.questions:
            options = {
                'label': question.label,
                'required': question.required,
                'initial': initial_answers.get(question.id, ''),
            }
            if question.kind == Kind.LONG_TEXT:
                field = forms.CharField(max_length=3000, widget=forms.Textarea(attrs={'rows': 3}), **options)
            elif question.kind == Kind.CHOICE:
                choices = [('', 'Selecione...')] + [(c, c) for c in question.choice_list()]
                field = forms.ChoiceField(choices=choices, **options)
            elif question.kind == Kind.YES_NO:
                field = forms.ChoiceField(choices=[('', 'Selecione...'), ('Sim', 'Sim'), ('Não', 'Não')], **options)
            else:
                field = forms.CharField(max_length=500, **options)
            field.widget.attrs['class'] = 'form-select' if isinstance(field, forms.ChoiceField) else 'form-control'
            self.fields[f'q_{question.id}'] = field

    def save(self, registration):
        for question in self.questions:
            RegistrationAnswer.objects.update_or_create(
                registration=registration,
                question=question,
                defaults={'value': self.cleaned_data.get(f'q_{question.id}') or ''},
            )
