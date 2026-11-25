from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from .fields import ProtectedDocumentField
from datetime import timedelta
from uuid import uuid4


def import_batch_expiry():
    return timezone.now() + timedelta(hours=24)

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


class DocumentImportBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=import_batch_expiry, db_index=True)


class DocumentImportCandidate(models.Model):
    """A proposed expiry matter, never eligible for reminders until explicitly saved."""
    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready for review"
        FAILED = "failed", "Processing failed"
        SAVING = "saving", "Saving"
        SAVED = "saved", "Saved"
        SKIPPED = "skipped", "Skipped"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    batch = models.ForeignKey(DocumentImportBatch, on_delete=models.CASCADE, related_name="candidates")
    source_key = models.UUIDField()
    filename = models.CharField(max_length=255)
    file_size = models.PositiveBigIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.WAITING)
    suggested_name = models.CharField(max_length=200, blank=True)
    suggested_expiry_date = models.DateField(null=True, blank=True)
    suggested_notes = models.TextField(blank=True)
    date_evidence = models.TextField(blank=True)
    warnings = models.JSONField(default=list)
    scan_sha256 = models.CharField(max_length=64, blank=True)
    scan_token = models.TextField(blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    created_item = models.OneToOneField(ExpiryItem, on_delete=models.SET_NULL, null=True, blank=True)
    saved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
