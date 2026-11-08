from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from .fields import ProtectedDocumentField

class ExpiryItem(models.Model):
    user = models.ForeignKey(
    User,
    on_delete=models.CASCADE,
    null=True,
    blank=True
)
    name = models.CharField(max_length=200)
    expiry_date = models.DateField()
    notes = models.TextField(blank=True)

    document = ProtectedDocumentField(
    upload_to="documents/",
    blank=True,
    null=True
)
    created_at = models.DateTimeField(auto_now_add=True)
    @property
    def days_remaining(self):
        today = timezone.localdate()
        return (self.expiry_date - today).days
    def __str__(self):
        return self.name

# Create your models here.


class DocumentDeletion(models.Model):
    backend = models.CharField(max_length=255)
    name = models.CharField(max_length=500)
    delivery_type = models.CharField(max_length=20, default="authenticated")
    attempts = models.PositiveIntegerField(default=0)
    retry_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    last_error = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["backend", "name", "delivery_type"], name="unique_document_deletion")]


class ReminderDelivery(models.Model):
    item = models.ForeignKey(ExpiryItem, on_delete=models.CASCADE)
    expiry_date = models.DateField()
    days_before = models.PositiveSmallIntegerField()
    sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    retry_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    last_error = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "expiry_date", "days_before"], name="unique_expiry_reminder"),
            models.CheckConstraint(condition=models.Q(days_before__in=[30, 14, 7, 1]), name="valid_reminder_interval"),
        ]
