"""Materiais pós-evento: cadastro e acesso de quem participou."""

import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from attendance.models import Attendance
from core.permissions import can_manage
from registrations.models import Registration

from ..forms import MaterialForm
from ..models import Activity, Material
from .common import CONFIRMED, managed_activity


def participated(registration):
    return Attendance.objects.filter(registration=registration).exists()


def can_access_material(user, material):
    """A equipe sempre acessa. O participante, depois da liberação e conforme o público do material."""
    activity = material.activity
    if can_manage(user, activity):
        return True
    if not user.is_authenticated or activity.status == Activity.Status.CANCELLED or not material.is_released():
        return False
    registration = Registration.objects.filter(user=user, activity=activity, status=CONFIRMED).first()
    if registration is None:
        return False
    if material.audience == Material.Audience.ATTENDED and activity.sessions.exists():
        return participated(registration)
    return True


def materials_for(user, activity, registration=None, attended=None):
    """Materiais que a pessoa pode abrir. Mesma regra de can_access_material, em poucas consultas no total
    (antes eram três por material). Quem já tem em mãos a inscrição confirmada e se a pessoa participou
    (ex.: "Minhas inscrições", DES-03) passa os dois e evita mais consultas por atividade."""
    materials = list(activity.materials.all())
    if not materials or can_manage(user, activity):
        return materials
    if not user.is_authenticated or activity.status == Activity.Status.CANCELLED:
        return []
    if registration is None:
        registration = Registration.objects.filter(user=user, activity=activity, status=CONFIRMED).first()
    if registration is None:
        return []
    released = []
    for material in materials:
        if not material.is_released():
            continue
        if material.audience == Material.Audience.ATTENDED:
            if attended is None:
                attended = not activity.sessions.exists() or participated(registration)
            if not attended:
                continue
        released.append(material)
    return released


@login_required
def material_create(request, activity_id):
    activity, allowed = managed_activity(request, activity_id)
    if not allowed:
        raise PermissionDenied('Acesso restrito.')
    form = MaterialForm(request.POST or None, request.FILES or None, activity=activity)
    if form.is_valid():
        form.save()
        messages.success(request, 'Material adicionado.')
        return redirect('activity_manage', pk=activity.pk)
    return render(request, 'organizer/simple_form.html', {'form': form, 'activity': activity, **MATERIAL_TEXT})


@login_required
def material_edit(request, pk):
    material = get_object_or_404(Material.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, material.activity):
        raise PermissionDenied('Acesso restrito.')
    form = MaterialForm(request.POST or None, request.FILES or None, instance=material)
    if form.is_valid():
        form.save()
        messages.success(request, 'Material atualizado.')
        return redirect('activity_manage', pk=material.activity_id)
    return render(
        request,
        'organizer/simple_form.html',
        {'form': form, 'activity': material.activity, **MATERIAL_TEXT, 'heading': 'Editar material',
         'current_file': material.file.name.split('/')[-1] if material.file else ''},
    )  # fmt: skip


@login_required
def material_delete(request, pk):
    material = get_object_or_404(Material.objects.select_related('activity'), pk=pk)
    if not can_manage(request.user, material.activity):
        raise PermissionDenied('Acesso restrito.')
    if request.method == 'POST':
        if material.file:
            material.file.delete(save=False)
        material.delete()
        messages.success(request, 'Material removido.')
        return redirect('activity_manage', pk=material.activity_id)
    return render(
        request,
        'organizer/confirm_delete.html',
        {'object': material, 'heading': 'Remover material',
         'cancel_url': reverse('activity_manage', args=[material.activity_id])},
    )  # fmt: skip


@login_required
def material_open(request, pk):
    """Entrega o arquivo (ou redireciona para o link) só para quem tem acesso."""
    material = get_object_or_404(Material.objects.select_related('activity'), pk=pk)
    if not can_access_material(request.user, material):
        raise Http404
    if material.url:
        return redirect(material.url)
    try:
        handle = material.file.open('rb')
    except FileNotFoundError as exc:
        raise Http404 from exc
    name = material.file.name.split('/')[-1]
    content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    # Só PDF e imagens abrem no navegador; o resto é baixado. Nada é interpretado como página do site.
    inline = content_type in INLINE_TYPES
    response = FileResponse(
        handle,
        as_attachment=not inline,
        filename=name,
        content_type=content_type if inline else 'application/octet-stream',
    )
    if content_type != 'application/pdf':  # o leitor de PDF do Chrome não abre com sandbox
        response['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    response['X-Content-Type-Options'] = 'nosniff'
    return response


INLINE_TYPES = {'application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp'}


MATERIAL_TEXT = {
    'heading': 'Novo material',
    'intro': 'Slides, apostila ou gravação. O arquivo não fica público: só quem tem acesso consegue abrir.',
}
