"""Campo "Matrícula ou CPF" do cadastro.

Aluno: digita a matrícula, que precisa ser a mesma do e-mail institucional (12546456@aluno.fsa.br). Ela só
passa a valer como matrícula de aluno depois que o código enviado para esse e-mail é confirmado.
Convidado: pode informar o CPF (validado pelos dígitos e guardado no formato 000.000.000-00).
"""

import re

from django import forms

CPF_FORMATTED = re.compile(r'^\d{3}\.\d{3}\.\d{3}-\d{2}$')


def cpf_digits(value):
    """Os 11 dígitos se o valor tem cara de CPF (com ou sem pontos e traço), senão ''."""
    digits = re.sub(r'[.\-]', '', value or '')
    return digits if len(digits) == 11 and digits.isdigit() else ''


def valid_cpf(digits):
    if len(digits) != 11 or not digits.isdigit() or digits == digits[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(d) * w for d, w in zip(digits[:size], range(size + 1, 1, -1), strict=True))
        check = (total * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


def format_cpf(digits):
    return f'{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}'


def is_cpf(value):
    return bool(CPF_FORMATTED.match((value or '').strip()))


def mask_cpf(value):
    """123.456.789-09 vira ***.456.789-** (como nos sites do governo): confirma a pessoa sem expor o CPF."""
    value = (value or '').strip()
    return f'***.{value[4:11]}-**' if is_cpf(value) else value


def clean_document(value, email, site):
    """Valida o campo conforme o e-mail informado. Devolve o valor a gravar ou levanta ValidationError."""
    value = re.sub(r'\s+', '', value or '')
    student_id = site.student_id_from_email(email)
    if student_id:
        if not value:
            raise forms.ValidationError(
                'Informe a sua matrícula: a mesma que aparece antes do @ no e-mail institucional.'
            )
        if value.lower() != student_id:
            raise forms.ValidationError(
                f'A matrícula precisa ser a mesma do e-mail institucional ({email}). Confira os dois campos.'
            )
        return value
    if not value:
        return ''
    digits = cpf_digits(value)
    if digits:
        if not valid_cpf(digits):
            raise forms.ValidationError('CPF inválido. Confira os números.')
        return format_cpf(digits)
    domains = site.student_domains()
    if domains:
        listed = ' ou '.join(f'@{d}' for d in domains)
        raise forms.ValidationError(
            f'Matrícula só com o e-mail institucional ({listed}). Com outro e-mail, informe o CPF ou deixe em branco.'
        )
    return value
