from django.conf import settings
from django.db import models

from activities.models import Session
from registrations.models import Registration


class Attendance(models.Model):
    class Method(models.TextChoices):
        QR_CODE = 'QR_CODE', 'QR Code'
        MANUAL = 'MANUAL', 'Manual'

    registration = models.ForeignKey(Registration, on_delete=models.CASCADE, related_name='attendances')
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='attendances')
    check_in = models.DateTimeField(auto_now_add=True)
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='validated_attendances'
    )
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.QR_CODE)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['registration', 'session'], name='unique_registration_session_attendance')
        ]

    def __str__(self):
        return f'{self.registration} - {self.session or self.check_in}'
