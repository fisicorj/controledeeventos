from django import forms, template

register = template.Library()


@register.filter
def field_kind(field):
    """Tipo de campo para escolher o layout no template de formulário."""
    widget = field.field.widget
    if isinstance(widget, forms.CheckboxInput):
        return 'checkbox'
    if isinstance(widget, (forms.Select, forms.SelectMultiple)):
        return 'select'
    if isinstance(widget, forms.ClearableFileInput):
        return 'file'
    if getattr(widget, 'input_type', '') == 'color':
        return 'color'
    return 'input'


@register.filter
def styled(field):
    """Renderiza o campo com as classes do Bootstrap e o estado de erro."""
    kind = field_kind(field)
    css = {'checkbox': 'form-check-input', 'select': 'form-select', 'color': 'form-control form-control-color'}.get(
        kind, 'form-control'
    )
    if field.errors:
        css += ' is-invalid'
    attrs = {'class': css}
    if field.help_text:
        attrs['aria-describedby'] = f'{field.auto_id}_help'
    return field.as_widget(attrs=attrs)


@register.filter
def initials(user):
    name = (user.get_full_name() or user.username).split()
    return ''.join(part[0] for part in name[:2]).upper()
