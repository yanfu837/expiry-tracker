"""Durable, retryable deletion of files after their database references commit."""
import logging
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.module_loading import import_string

from .models import DocumentDeletion, ExpiryItem
from .storage import PRIVATE_PREFIX

logger = logging.getLogger(__name__)


def queue_document_deletion(name):
    if not name:
        return
    job, _ = DocumentDeletion.objects.get_or_create(
        backend=settings.STORAGES["default"]["BACKEND"], name=name,
        delivery_type="authenticated" if name.startswith(PRIVATE_PREFIX) else "upload",
    )
    transaction.on_commit(lambda pk=job.pk: delete_document(pk, force=True))


def delete_document(pk, force=False):
    now = timezone.now()
    token = uuid4()
    candidates = DocumentDeletion.objects.filter(pk=pk).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
    if not force:
        candidates = candidates.filter(retry_at__lte=now)
    if not candidates.update(lease_token=token, lease_until=now+timedelta(minutes=2), attempts=F("attempts")+1):
        return None
    job = DocumentDeletion.objects.get(pk=pk, lease_token=token)
    if ExpiryItem.objects.filter(document=job.name).exists():
        DocumentDeletion.objects.filter(pk=pk, lease_token=token).update(lease_until=None, lease_token=None, retry_at=now+timedelta(minutes=15))
        return None
    try:
        storage = default_storage if job.backend == settings.STORAGES["default"]["BACKEND"] else import_string(job.backend)()
        if hasattr(storage, "delete_asset"):
            storage.delete_asset(job.name, job.delivery_type)
        else:
            storage.delete(job.name)
    except Exception as error:
        delay = min(24*60, 15 * 2**min(job.attempts-1, 7))
        DocumentDeletion.objects.filter(pk=pk, lease_token=token).update(
            lease_token=None, lease_until=None, retry_at=now+timedelta(minutes=delay), last_error=type(error).__name__[:100],
        )
        logger.warning("Document cleanup %s will be retried (%s)", pk, type(error).__name__)
        return False
    DocumentDeletion.objects.filter(pk=pk, lease_token=token).delete()
    return True


def cleanup_documents(limit=200):
    now = timezone.now()
    candidates = list(DocumentDeletion.objects.filter(retry_at__lte=now).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lte=now),
    ).order_by("pk").values_list("pk", flat=True)[:limit])
    results = [delete_document(pk) for pk in candidates]
    return {"deleted": results.count(True), "failed": results.count(False), "pending": DocumentDeletion.objects.count()}
