from django import forms

from core.text import single_line

from .models import Activity, Announcement, Material, RegistrationQuestion, Session, Speaker, SpotCategory


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = 'datetime-local'


S = Activity.Status
STATUS_TRANSITIONS = {
    S.DRAFT: {S.DRAFT, S.PUBLISHED, S.CANCELLED},
    S.PUBLISHED: {S.PUBLISHED, S.DRAFT, S.CANCELLED, S.FINISHED},
    S.FINISHED: {S.FINISHED},  # finalizada é definitiva: certificados e pesquisa já saíram
    S.CANCELLED: {S.CANCELLED},  # cancelada é definitiva: os inscritos já foram avisados
}
STATUS_HELP = {
    S.FINISHED: 'Atividade finalizada: a situação não muda mais. Presenças esquecidas podem ser lançadas à mão.',
    S.CANCELLED: 'Atividade cancelada: a situação não muda mais. Para fazer de novo, use "Duplicar".',
    S.PUBLISHED: 'Para encerrar, prefira "Finalizar atividade" na gestão: mostra antes quem vai receber certificado.',
}


# Mudanças que o inscrito precisa saber (ele pode ter se programado para a data e o local antigos).
NOTIFY_FIELDS = ('start_date', 'end_date', 'location')


class ActivityForm(forms.ModelForm):
    notify_change = forms.BooleanField(
        label='Avisar os inscritos por e-mail se a data ou o local mudarem',
        required=False,
        initial=True,
        help_text='Vai para confirmados e lista de espera, com o antes e o depois.',
    )

    class Meta:
        model = Activity
        fields = [
            'title',
            'description',
            'activity_type',
            'start_date',
            'end_date',
            'registration_start',
            'registration_end',
            'capacity',
            'location',
            'image',
            'status',
            'waitlist_enabled',
            'waitlist_offer_hours',
            'access_code',
            'certificate_enabled',
            'workload_hours',
            'min_attendance_percent',
            'survey_enabled',
            'survey_required',
        ]
        widgets = {
            k: DateTimeLocalInput(format='%Y-%m-%dT%H:%M')
            for k in ['start_date', 'end_date', 'registration_start', 'registration_end']
        }
        labels = {
            'title': 'Título',
            'description': 'Descrição',
            'activity_type': 'Tipo',
            'start_date': 'Início',
            'end_date': 'Término',
            'registration_start': 'Início das inscrições',
            'registration_end': 'Fim das inscrições',
            'capacity': 'Vagas',
            'location': 'Local',
            'image': 'Imagem de capa',
            'status': 'Situação',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            is_checkbox = isinstance(f.widget, forms.CheckboxInput)
            f.widget.attrs['class'] = 'form-check-input' if is_checkbox else 'form-control'
        self.fields['description'].widget.attrs['rows'] = 5
        self.fields['waitlist_offer_hours'].required = False
        has_people = (
            self.instance.pk and self.instance.registrations.filter(status__in=['CONFIRMED', 'WAITLISTED']).exists()
        )
        if not has_people:
            del self.fields['notify_change']  # sem inscritos, não há a quem avisar
        # Só as mudanças de situação que fazem sentido a partir da situação atual.
        allowed = self.allowed_statuses()
        status = self.fields['status']
        status.choices = [(value, label) for value, label in Activity.Status.choices if value in allowed]
        status.help_text = STATUS_HELP.get(self.instance.status if self.instance.pk else None, '')

    def allowed_statuses(self):
        if not self.instance.pk:
            return {Activity.Status.DRAFT, Activity.Status.PUBLISHED}
        current = self.instance.status
        allowed = set(STATUS_TRANSITIONS[current])
        if current == Activity.Status.PUBLISHED and self.instance.registrations.exclude(status='CANCELLED').exists():
            allowed.discard(Activity.Status.DRAFT)  # com inscritos, não volta a rascunho (sumiria da lista deles)
        return allowed

    def clean_title(self):
        return single_line(self.cleaned_data.get('title'))

    def clean_waitlist_offer_hours(self):
        # Em branco (ou formulário antigo sem o campo): mantém o prazo atual, ou o padrão de 24 horas.
        hours = self.cleaned_data.get('waitlist_offer_hours')
        if hours is None:
            return self.instance.waitlist_offer_hours if self.instance.pk else 24
        return hours

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image and hasattr(image, 'size') and image.size > 5 * 1024 * 1024:
            raise forms.ValidationError('A imagem de capa deve ter no máximo 5 MB.')
        return image

    def clean_status(self):
        status = self.cleaned_data['status']
        if status not in self.allowed_statuses():
            raise forms.ValidationError('Esta mudança de situação não é permitida.')
        return status


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ['title', 'start_at', 'end_at', 'location', 'speakers', 'check_in_open']
        labels = {
            'title': 'Título',
            'start_at': 'Início',
            'end_at': 'Término',
            'location': 'Local',
            'check_in_open': 'Check-in aberto',
        }
        help_texts = {'location': 'Em branco: usa o local da atividade.'}
        widgets = {
            'start_at': DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
            'end_at': DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, activity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if activity is not None:
            self.instance.activity = activity
        speakers = self.fields['speakers']
        speakers.widget = forms.CheckboxSelectMultiple()
        speakers.queryset = (
            Speaker.objects.filter(activity=self.instance.activity)
            if self.instance.activity_id
            else Speaker.objects.none()
        )
        speakers.help_text = (
            'Cadastre os palestrantes na página de gestão da atividade.' if not speakers.queryset.exists() else ''
        )
        for name, f in self.fields.items():
            if name == 'speakers':
                continue
            f.widget.attrs['class'] = 'form-check-input' if name == 'check_in_open' else 'form-control'


class QuestionForm(forms.ModelForm):
    class Meta:
        model = RegistrationQuestion
        fields = ['label', 'kind', 'choices', 'required', 'order']
        widgets = {'choices': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, activity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if activity is not None:
            self.instance.activity = activity
            if not self.instance.pk:
                self.initial.setdefault('order', activity.questions.count() + 1)
        for name, f in self.fields.items():
            if name == 'required':
                f.widget.attrs['class'] = 'form-check-input'
            elif name == 'kind':
                f.widget.attrs['class'] = 'form-select'
            else:
                f.widget.attrs['class'] = 'form-control'


class SpeakerForm(forms.ModelForm):
    class Meta:
        model = Speaker
        fields = ['name', 'role', 'bio', 'photo', 'link', 'order']
        widgets = {'bio': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, activity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if activity is not None:
            self.instance.activity = activity
            if not self.instance.pk:
                self.initial.setdefault('order', activity.speakers.count() + 1)
        self.fields['photo'].widget.attrs['accept'] = 'image/*'

    def clean_photo(self):
        photo = self.cleaned_data.get('photo')
        if photo and hasattr(photo, 'size') and photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError('A foto deve ter no máximo 2 MB.')
        return photo


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['audience', 'subject', 'body', 'show_on_page']
        widgets = {'body': forms.Textarea(attrs={'rows': 8}), 'audience': forms.RadioSelect}

    def clean_subject(self):
        return single_line(self.cleaned_data.get('subject'))


class CategoryForm(forms.ModelForm):
    class Meta:
        model = SpotCategory
        fields = ['name', 'capacity', 'description', 'access_code', 'requires_document', 'order']

    def __init__(self, *args, activity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if activity is not None:
            self.instance.activity = activity
            if not self.instance.pk:
                self.initial.setdefault('order', activity.categories.count() + 1)

    def clean_capacity(self):
        capacity = self.cleaned_data['capacity']
        if self.instance.pk:
            taken = self.instance.registrations.filter(status='CONFIRMED').count()
            if capacity < taken:
                raise forms.ValidationError(f'Já há {taken} inscritos confirmados neste tipo de vaga.')
        return capacity

    def validate_unique(self):
        # activity não está nos campos do formulário, então a regra de nome único é conferida aqui.
        super().validate_unique()
        name = self.cleaned_data.get('name')
        if (
            name
            and SpotCategory.objects.filter(activity=self.instance.activity, name__iexact=name)
            .exclude(pk=self.instance.pk)
            .exists()
        ):
            self.add_error('name', 'Já existe um tipo de vaga com este nome nesta atividade.')


# Tipos de arquivo aceitos como material (páginas HTML/SVG ficam de fora: poderiam rodar código no site).
MATERIAL_EXTENSIONS = {
    'pdf', 'ppt', 'pptx', 'pps', 'ppsx', 'odp', 'key', 'doc', 'docx', 'odt', 'rtf', 'txt', 'md',
    'xls', 'xlsx', 'ods', 'csv', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'zip', 'rar', '7z',
    'mp3', 'mp4', 'm4a', 'ipynb', 'py', 'c', 'cs', 'java', 'sql',
}  # fmt: skip


class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = ['title', 'description', 'file', 'url', 'audience', 'available_from', 'order']
        widgets = {
            'available_from': DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
            'audience': forms.RadioSelect,
        }

    def __init__(self, *args, activity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if activity is not None:
            self.instance.activity = activity

    def clean_file(self):
        file = self.cleaned_data.get('file')
        if file and hasattr(file, 'size'):
            if file.size > 50 * 1024 * 1024:
                raise forms.ValidationError('O arquivo deve ter no máximo 50 MB. Para vídeos, use um link.')
            extension = file.name.rsplit('.', 1)[-1].lower() if '.' in file.name else ''
            if extension not in MATERIAL_EXTENSIONS:
                raise forms.ValidationError(
                    'Tipo de arquivo não aceito. Use PDF, apresentações, documentos, planilhas, imagens ou ZIP.'
                )
        return file


class DuplicateForm(forms.Form):
    title = forms.CharField(label='Título da nova edição', max_length=200)
    start_date = forms.DateTimeField(
        label='Início da nova edição',
        widget=DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
        help_text='Término, período de inscrição e sessões acompanham, mantendo os mesmos intervalos.',
    )

    def clean_title(self):
        return single_line(self.cleaned_data.get('title'))
