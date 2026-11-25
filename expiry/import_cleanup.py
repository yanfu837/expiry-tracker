"""Remove expired draft metadata; committed expiry matters remain intact."""
from django.utils import timezone

from .models import DocumentImportBatch


def cleanup_import_batches(limit=100):
    now = timezone.now()
    candidates = DocumentImportBatch.objects.filter(expires_at__lte=now).exclude(
        candidates__lease_until__gt=now).order_by("expires_at").distinct()
    ids = list(candidates.values_list("pk", flat=True)[:max(1, min(limit, 1000))])
    if ids:
        DocumentImportBatch.objects.filter(pk__in=ids).delete()
    return len(ids)
