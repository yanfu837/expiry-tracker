"""Staged expiry matters: a scanned file is not a reminder or a saved item."""
import hashlib
import json
import logging
from datetime import timedelta
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from document_service.formats import MAX_BYTES, MIME_TYPES
from .deletions import queue_document_deletion
from .document_processing import (DocumentProcessingError, field_suggestions, make_scan_token,
                                  process_upload, processor_configured, read_upload)
from .forms import DocumentAnalysisForm, ExpiryItemForm
from .models import DocumentImportBatch, DocumentImportCandidate
from .import_cleanup import cleanup_import_batches

logger = logging.getLogger(__name__)
Status = DocumentImportCandidate.Status


def _error(message, status=400, **extra):
    return JsonResponse({"message": message, **extra}, status=status)


def _free(now):
    return Q(lease_until__isnull=True) | Q(lease_until__lte=now)


def _candidate(request, batch_id, candidate_id):
    return get_object_or_404(DocumentImportCandidate.objects.select_related("batch", "created_item"),
                             pk=candidate_id, batch_id=batch_id, batch__user=request.user)


def _serialize(row):
    item = row.created_item if row.created_item_id and row.created_item.user_id == row.batch.user_id else None
    return {"id": str(row.pk), "source_key": str(row.source_key), "filename": row.filename,
            "size": row.file_size, "status": row.status, "name": item.name if item else row.suggested_name,
            "expiry_date": (item.expiry_date.isoformat() if item else
                            row.suggested_expiry_date.isoformat() if row.suggested_expiry_date else None),
            "notes": item.notes if item else row.suggested_notes, "evidence": row.date_evidence,
            "warnings": row.warnings, "message": row.last_error,
            "busy": bool(row.lease_until and row.lease_until > timezone.now()),
            "item_url": reverse("edit_item", args=[item.pk]) if item else None,
            "saved": row.saved_at is not None,
            "urls": {action: reverse("batch_"+action, args=[row.batch_id, row.pk])
                     for action in ("analyze", "save", "skip", "duplicate")}}


def _snapshot(batch):
    return {"id": str(batch.pk), "expired": batch.expires_at <= timezone.now(),
            "expires_at": batch.expires_at.isoformat(),
            "url": reverse("batch_detail", args=[batch.pk]),
            "status_url": reverse("batch_status", args=[batch.pk]),
            "candidates": [_serialize(row) for row in batch.candidates.select_related("batch", "created_item").order_by("created_at", "id")]}


def _expired(row):
    return row.batch.expires_at <= timezone.now()


@login_required
@never_cache
@require_GET
def page(request, batch_id=None):
    batch = get_object_or_404(DocumentImportBatch, pk=batch_id, user=request.user) if batch_id else None
    return render(request, "expiry/batch_import.html", {
        "initial_batch": _snapshot(batch) if batch else None,
        "max_files": settings.DOCUMENT_BATCH_MAX_FILES, "accepted_types": ",".join(MIME_TYPES),
        "max_total_bytes": settings.DOCUMENT_BATCH_MAX_TOTAL_BYTES,
        "processor_available": processor_configured(),
    })


@login_required
@never_cache
@require_GET
def status(request, batch_id):
    return JsonResponse(_snapshot(get_object_or_404(DocumentImportBatch, pk=batch_id, user=request.user)))


@login_required
@never_cache
@require_POST
def create(request):
    try:
        if len(request.body) > 16*1024:
            raise ValueError()
        payload = json.loads(request.body)
        files = payload["files"]
        if not isinstance(files, list) or not 1 <= len(files) <= settings.DOCUMENT_BATCH_MAX_FILES:
            raise ValueError()
        sources = []
        for file in files:
            key = UUID(file["key"])
            name = PurePosixPath(file["name"].replace("\\", "/")).name
            size = file["size"]
            if (not name or len(name) > 255 or type(size) is not int or not 0 < size <= MAX_BYTES):
                raise ValueError()
            valid_file = 0 < size <= MAX_BYTES and PurePosixPath(name).suffix.lower() in MIME_TYPES
            sources.append((key, name, size, valid_file))
        if len({source[0] for source in sources}) != len(sources):
            raise ValueError()
        if sum(source[2] for source in sources) > settings.DOCUMENT_BATCH_MAX_TOTAL_BYTES:
            return _error("These files exceed 50 MB in total. Choose fewer files or create an item without a document.")
    except (ValueError, KeyError, TypeError, AttributeError):
        return _error(f"Choose 1 to {settings.DOCUMENT_BATCH_MAX_FILES} supported files, up to 10 MB each.")
    cleanup_import_batches(limit=20)
    with transaction.atomic():
        batch = DocumentImportBatch.objects.create(user=request.user)
        DocumentImportCandidate.objects.bulk_create([
            DocumentImportCandidate(batch=batch, source_key=key, filename=name, file_size=size,
                status=Status.WAITING if valid else Status.FAILED,
                last_error="" if valid else "Unsupported, empty or oversized file. Select another file or skip this row.")
            for key, name, size, valid in sources
        ])
    return JsonResponse(_snapshot(batch), status=201)


@login_required
@never_cache
@require_POST
def analyze(request, batch_id, candidate_id):
    row = _candidate(request, batch_id, candidate_id)
    if row.saved_at:
        return JsonResponse({"candidate": _serialize(row)})
    if _expired(row):
        return _error("This draft batch has expired. Start a new batch; saved items are kept.", 410)
    form = DocumentAnalysisForm(request.POST, request.FILES)
    if not form.is_valid():
        return _error("Reselect the original file for this row, up to 10 MB.")
    document = form.cleaned_data["document"]
    if document.size != row.file_size or PurePosixPath(document.name).suffix.lower() != PurePosixPath(row.filename).suffix.lower():
        return _error("This file does not match the original source. Reselect the original file or start a new batch.")
    if row.scan_sha256 and hashlib.sha256(read_upload(document)).hexdigest() != row.scan_sha256:
        return _error("The source content has changed. Start a new batch and review it again.", 409)
    token, now = uuid4(), timezone.now()
    claimed = DocumentImportCandidate.objects.filter(pk=row.pk, saved_at__isnull=True,
        batch__expires_at__gt=now).exclude(status=Status.SAVING).filter(_free(now)).update(
        status=Status.PROCESSING,
        lease_token=token, lease_until=now+timedelta(minutes=3), last_error="")
    if not claimed:
        row.refresh_from_db()
        return _error("This document is still being processed. Please try again shortly.", 409, candidate=_serialize(row))
    try:
        payload = process_upload(document, extract=True)
        fields = field_suggestions(payload, document.name)
        updated = DocumentImportCandidate.objects.filter(pk=row.pk, lease_token=token, status=Status.PROCESSING).update(
            status=Status.READY, suggested_name=fields["name"], suggested_expiry_date=fields["expiry_date"],
            suggested_notes=fields["notes"], date_evidence=fields["evidence"], warnings=fields["warnings"],
            scan_sha256=payload["sha256"], scan_token=make_scan_token(document, request.user.pk),
            lease_token=None, lease_until=None, last_error="")
        if not updated:
            return _error("The processing status has changed. Refresh this batch.", 409)
    except (DocumentProcessingError, OSError, ValueError, TypeError, AttributeError) as error:
        message = str(error) if isinstance(error, DocumentProcessingError) else "Scanning or extraction did not finish. Retry this document individually."
        DocumentImportCandidate.objects.filter(pk=row.pk, lease_token=token).update(
            status=Status.FAILED, lease_token=None, lease_until=None, last_error=message)
        row.refresh_from_db()
        return _error(message, 422 if getattr(error, "code", "") in {"infected", "invalid_file"} else 503,
                      candidate=_serialize(row))
    row.refresh_from_db()
    return JsonResponse({"candidate": _serialize(row)})


def _release(row_id, token, message):
    DocumentImportCandidate.objects.filter(pk=row_id, lease_token=token, saved_at__isnull=True).update(
        status=Status.READY, lease_token=None, lease_until=None, last_error=message)


def _discard_upload(name):
    if not name:
        return
    try:
        queue_document_deletion(name)
    except Exception:
        try:
            default_storage.delete(name)
        except Exception:
            logger.error("Batch upload cleanup could not be completed")


@login_required
@never_cache
@require_POST
def save(request, batch_id, candidate_id):
    row = _candidate(request, batch_id, candidate_id)
    # A saved tombstone survives deletion of the item: retry must not recreate it.
    if row.saved_at:
        return JsonResponse({"candidate": _serialize(row), "already_saved": True})
    if _expired(row):
        return _error("This draft batch has expired. Start a new batch; saved items are kept.", 410)
    if request.POST.get("confirmed") != "true":
        return _error("Review this expiry item and select its confirmation checkbox before saving.")
    if row.status not in {Status.READY, Status.SAVING} or not row.scan_sha256:
        return _error("Scanning and extraction must finish before this item can be saved.", 409)
    document = request.FILES.get("document")
    if not document:
        return _error("Reselect the original file before saving.")
    try:
        if hashlib.sha256(read_upload(document)).hexdigest() != row.scan_sha256:
            return _error("The file content has changed. Extract and review it again.", 409)
    except DocumentProcessingError as error:
        return _error(str(error))
    token, now = uuid4(), timezone.now()
    claimed = DocumentImportCandidate.objects.filter(pk=row.pk, saved_at__isnull=True,
        status__in=[Status.READY, Status.SAVING], batch__expires_at__gt=now).filter(_free(now)).update(
        status=Status.SAVING, lease_token=token, lease_until=now+timedelta(minutes=5), last_error="")
    if not claimed:
        row.refresh_from_db()
        return _error("This item is being saved. Check its status shortly.", 409, candidate=_serialize(row))
    data = request.POST.copy()
    data["scan_token"] = row.scan_token
    form = ExpiryItemForm(data, request.FILES, user=request.user)
    if not form.is_valid():
        _release(row.pk, token, "Check the name, expiry date and document. This item was not saved.")
        return _error("Check the name, expiry date and document. This item was not saved.", errors=form.errors.get_json_data())
    stored_name = None
    try:
        document = form.cleaned_data["document"]
        stored_name = default_storage.save("documents/"+document.name, document)
        with transaction.atomic():
            locked = DocumentImportCandidate.objects.select_for_update().get(pk=row.pk)
            if locked.saved_at or locked.lease_token != token or locked.status != Status.SAVING:
                raise ValueError("Import lease changed")
            item = form.save(commit=False)
            item.user = request.user
            item.document = stored_name
            item.save()
            locked.created_item, locked.saved_at, locked.status = item, timezone.now(), Status.SAVED
            locked.lease_token = locked.lease_until = None
            locked.last_error = ""
            locked.save(update_fields=["created_item", "saved_at", "status", "lease_token", "lease_until", "last_error"])
        stored_name = None  # The committed item now owns the attachment.
    except Exception:
        _discard_upload(stored_name)
        _release(row.pk, token, "This item was not saved. You can retry; other saved items are kept.")
        return _error("This item was not saved. You can retry; other saved items are kept.", 503)
    row.refresh_from_db()
    return JsonResponse({"candidate": _serialize(row), "already_saved": False}, status=201)


@login_required
@never_cache
@require_POST
def skip(request, batch_id, candidate_id):
    row = _candidate(request, batch_id, candidate_id)
    if _expired(row):
        return _error("This draft batch has expired.", 410)
    changed = DocumentImportCandidate.objects.filter(pk=row.pk, saved_at__isnull=True).filter(_free(timezone.now())).update(
        status=Status.SKIPPED, lease_token=None, lease_until=None, last_error="")
    if not changed:
        return _error("This item is saved or still being processed and cannot be skipped.", 409)
    row.refresh_from_db()
    return JsonResponse({"candidate": _serialize(row)})


@login_required
@never_cache
@require_POST
def duplicate(request, batch_id, candidate_id):
    row = _candidate(request, batch_id, candidate_id)
    if _expired(row):
        return _error("This draft batch has expired.", 410)
    if row.status not in {Status.READY, Status.SAVED} or not row.scan_sha256:
        return _error("Finish scanning and extracting this document first.", 409)
    with transaction.atomic():
        DocumentImportBatch.objects.select_for_update().get(pk=row.batch_id)
        if row.batch.candidates.count() >= settings.DOCUMENT_BATCH_MAX_CANDIDATES:
            return _error("This batch has reached its item limit. Start a new batch.")
        extra = DocumentImportCandidate.objects.create(
            batch=row.batch, source_key=row.source_key, filename=row.filename, file_size=row.file_size,
            status=Status.READY, suggested_name=row.suggested_name, suggested_notes=row.suggested_notes,
            date_evidence=row.date_evidence, warnings=["Another expiry item from the same document. Enter and confirm its expiry date."],
            scan_sha256=row.scan_sha256, scan_token=row.scan_token)
    return JsonResponse({"candidate": _serialize(extra)}, status=201)
