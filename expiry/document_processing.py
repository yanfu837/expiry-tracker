"""Only communicate with the operator's authenticated document service."""
import hashlib
import hmac
import json
import ipaddress
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from threading import BoundedSemaphore
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core import signing

from document_service.formats import MAX_BYTES, MIME_TYPES

_capacity = BoundedSemaphore(1)
_SALT = "expiry.document-scan.v1"
MESSAGES = {
    "unavailable": "The private scanning service is not configured or is unavailable. The file was not saved. Try again later.",
    "infected": "The file failed the security scan. Processing stopped. Please use another file.",
    "invalid_file": "This file cannot be processed. Use a supported document format, up to 10 MB.",
    "busy": "The service is processing another document. Please try again shortly.",
}


class DocumentProcessingError(OSError):
    def __init__(self, code="unavailable"):
        self.code = code if code in MESSAGES else "unavailable"
        super().__init__(MESSAGES[self.code])


@dataclass(frozen=True)
class ScanProof:
    sha256: str
    size: int
    verified_at: float


def processor_configured():
    return bool(settings.DOCUMENT_PROCESSOR_URL and len(settings.DOCUMENT_PROCESSOR_TOKEN) >= 32)


def field_suggestions(payload, filename):
    """Validate suggestions as data before returning or staging them for review."""
    from datetime import date
    fields = payload.get("fields", {})
    name = fields.get("name")
    if not isinstance(name, str) or not name.strip():
        name = PurePosixPath(filename).stem
    expiry = fields.get("expiry_date")
    if expiry is not None:
        expiry = date.fromisoformat(expiry).isoformat()
    notes, warnings = fields.get("notes", ""), fields.get("warnings", [])
    if not isinstance(notes, str) or not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
        raise ValueError("Invalid suggestions")
    return {"name": name[:200], "expiry_date": expiry, "notes": notes[:10000],
            "warnings": [w[:400] for w in warnings[:5]], "evidence": str(fields.get("evidence", ""))[:600],
            "method": "Local OCR and field extraction"}


def read_upload(upload):
    try:
        upload.seek(0)
        data = upload.read(MAX_BYTES + 1)
        if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
            raise DocumentProcessingError("invalid_file")
        return data
    finally:
        upload.seek(0)


def _call_processor(data, extension, extract):
    if not processor_configured():
        raise DocumentProcessingError()
    address = urlsplit(settings.DOCUMENT_PROCESSOR_URL)
    if (address.scheme not in {"http", "https"} or not address.hostname or address.username
            or address.password or address.query or address.fragment or address.path not in {"", "/"}):
        raise DocumentProcessingError()
    if address.scheme == "http":
        try:
            private = ipaddress.ip_address(address.hostname).is_private
        except ValueError:
            private = "." not in address.hostname or address.hostname.endswith((".internal", ".svc.cluster.local"))
        if not private:
            raise DocumentProcessingError()
    if not _capacity.acquire(blocking=False):
        raise DocumentProcessingError("busy")
    try:
        with requests.post(
            settings.DOCUMENT_PROCESSOR_URL.rstrip("/") + "/process", data=data,
            headers={"Authorization": "Bearer " + settings.DOCUMENT_PROCESSOR_TOKEN,
                     "Content-Type": "application/octet-stream", "X-Document-Extension": extension,
                     "X-Document-Mode": "extract" if extract else "scan"},
            timeout=(5, 100), stream=True, allow_redirects=False,
        ) as response:
            raw = bytearray()
            for chunk in response.iter_content(4096):
                raw.extend(chunk)
                if len(raw) > 32 * 1024:
                    raise DocumentProcessingError()
            payload = json.loads(raw)
            if response.status_code != 200:
                raise DocumentProcessingError(payload.get("error", "unavailable"))
        expected = hashlib.sha256(data).hexdigest()
        if (payload.get("scan") != "clean" or type(payload.get("size")) is not int
                or payload["size"] != len(data) or not isinstance(payload.get("sha256"), str)
                or not hmac.compare_digest(payload["sha256"], expected)):
            raise DocumentProcessingError()
        return payload
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
        raise DocumentProcessingError() from None
    finally:
        _capacity.release()


def process_upload(upload, extract=False):
    extension = PurePosixPath(upload.name).suffix.lower()
    if extension not in MIME_TYPES:
        raise DocumentProcessingError("invalid_file")
    data = read_upload(upload)
    digest = hashlib.sha256(data).hexdigest()
    proof = getattr(upload, "_document_scan_proof", None)
    if (not extract and isinstance(proof, ScanProof) and proof.sha256 == digest
            and proof.size == len(data) and 0 <= time.time()-proof.verified_at <= settings.DOCUMENT_SCAN_RECEIPT_TTL):
        return {"scan": "clean", "sha256": digest, "size": len(data)}
    payload = _call_processor(data, extension, extract)
    upload._document_scan_proof = ScanProof(digest, len(data), time.time())
    return payload


def make_scan_token(upload, user_id):
    proof = upload._document_scan_proof
    return signing.dumps({"user": user_id, "sha256": proof.sha256, "size": proof.size,
                          "extension": PurePosixPath(upload.name).suffix.lower()}, salt=_SALT)


def verify_scan_token(upload, user_id, token):
    if not user_id or not token or len(token) > 2048:
        return False
    try:
        payload = signing.loads(token, salt=_SALT, max_age=settings.DOCUMENT_SCAN_RECEIPT_TTL)
        data = read_upload(upload)
        digest = hashlib.sha256(data).hexdigest()
        if (payload["user"] != user_id or payload["sha256"] != digest or payload["size"] != len(data)
                or payload["extension"] != PurePosixPath(upload.name).suffix.lower()):
            return False
        # Keep the original token timestamp, never extend a scan's validity.
        timestamp = token.rsplit(":", 2)[1]
        verified_at = signing.b62_decode(timestamp)
        upload._document_scan_proof = ScanProof(digest, len(data), verified_at)
        return True
    except (signing.BadSignature, ValueError, TypeError, KeyError, AttributeError):
        return False
