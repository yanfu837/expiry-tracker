from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

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

    document = models.FileField(
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
