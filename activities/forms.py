from django import forms

from .models import Activity, RegistrationQuestion, Session


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = 'datetime-local'


class ActivityForm(forms.ModelForm):
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
            'certificate_enabled',
            'workload_hours',
            'min_attendance_percent',
        ]
        widgets = {
            k: DateTimeLocalInput(format='%Y-%m-%dT%H:%M')
            for k in ['start_date', 'end_date', 'registration_start', 'registration_end']
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            is_checkbox = isinstance(f.widget, forms.CheckboxInput)
            f.widget.attrs['class'] = 'form-check-input' if is_checkbox else 'form-control'
        self.fields['description'].widget.attrs['rows'] = 5


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ['title', 'start_at', 'end_at', 'location', 'check_in_open']
        widgets = {
            'start_at': DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
            'end_at': DateTimeLocalInput(format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, activity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if activity is not None:
            self.instance.activity = activity
        for name, f in self.fields.items():
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
