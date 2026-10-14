from django import forms

from activities.models import RegistrationQuestion, SpotCategory
from core import throttle

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


CODE_FAILS, CODE_PERIOD = 10, 3600  # códigos de acesso errados por pessoa e atividade, por hora


def _same_code(typed, expected):
    import hmac

    return hmac.compare_digest(typed.strip().casefold().encode(), expected.strip().casefold().encode())


class RegistrationOptionsForm(forms.Form):
    """Tipo de vaga e código de acesso, quando a atividade usa esses recursos."""

    category = forms.ModelChoiceField(
        label='Tipo de vaga',
        queryset=SpotCategory.objects.none(),
        widget=forms.RadioSelect,
        empty_label=None,
        error_messages={'required': 'Escolha o tipo de vaga.', 'invalid_choice': 'Escolha um tipo de vaga válido.'},
    )
    access_code = forms.CharField(label='Código de acesso do evento', max_length=40, required=False)
    category_code = forms.CharField(label='Código do tipo de vaga', max_length=40, required=False)

    def __init__(self, activity, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.activity, self.user = activity, user
        categories = activity.categories.all()
        self.has_categories = categories.exists()
        if self.has_categories:
            from .services import category_counts

            counts = category_counts(activity)

            def label(category):
                left = max(category.capacity - counts.get(category.id, 0), 0)
                status = f'{left} vaga{"s" if left != 1 else ""}' if left else 'esgotado, entra na lista de espera'
                extra = f' · {category.description}' if category.description else ''
                code = ' · exige código' if category.access_code else ''
                return f'{category.name} ({status}){extra}{code}'

            self.fields['category'].queryset = categories
            self.fields['category'].label_from_instance = label
        else:
            del self.fields['category']
        # Dois campos separados: o código geral do evento e o código de um tipo de vaga podem ser diferentes.
        if not activity.access_code:
            del self.fields['access_code']
        if not (self.has_categories and categories.exclude(access_code='').exists()):
            del self.fields['category_code']
        for name in ('access_code', 'category_code'):
            if name in self.fields:
                self.fields[name].widget.attrs.update(
                    {'class': 'form-control', 'autocomplete': 'off', 'autocapitalize': 'characters'}
                )

    def clean(self):
        data = super().clean()
        has_code_field = 'access_code' in self.fields or 'category_code' in self.fields
        if has_code_field and throttle.blocked(CODE_FAILS, CODE_PERIOD, 'access-code', self.user.pk, self.activity.pk):
            field = 'access_code' if 'access_code' in self.fields else 'category_code'
            self.add_error(field, 'Muitas tentativas com código errado. Espere uma hora ou fale com a organização.')
            return data
        code = (data.get('access_code') or '').strip()
        if self.activity.access_code and not _same_code(code, self.activity.access_code):
            self.add_error('access_code', 'Código de acesso inválido. Peça o código à organização.')
        category = data.get('category')
        if category is not None:
            category_code = (data.get('category_code') or '').strip()
            if category.access_code and not _same_code(category_code, category.access_code):
                self.add_error('category_code', f'"{category.name}" exige o código deste tipo de vaga.')
            if category.requires_document and not self.user.student_id_confirmed:
                self.add_error(
                    'category',
                    f'"{category.name}" é só para alunos da instituição, com a matrícula confirmada pelo e-mail '
                    'institucional. Ajuste em "Meus dados" (menu com o seu nome) ou escolha outro tipo.',
                )
        if self.has_error('access_code') or self.has_error('category_code'):
            throttle.hit(CODE_FAILS, CODE_PERIOD, 'access-code', self.user.pk, self.activity.pk)
        return data
