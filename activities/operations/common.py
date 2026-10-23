"""Peças usadas pelas ferramentas do organizador."""

from django.shortcuts import get_object_or_404

from core.permissions import can_manage
from registrations.models import Registration

from ..models import Activity

CONFIRMED = Registration.Status.CONFIRMED


def managed_activity(request, pk):
    activity = get_object_or_404(Activity, pk=pk)
    return activity, can_manage(request.user, activity)
