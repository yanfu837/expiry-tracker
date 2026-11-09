"""Private WSGI service: authenticate -> scan -> local parsing/OCR -> cleanup."""
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path

from .extraction import ProcessingFailure, extract_fields, read_document, run_tool, validate_signature
from .formats import MAX_BYTES, MIME_TYPES


def database_ready():
    directory = Path(os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav"))
    daily = [directory/name for name in ("daily.cvd", "daily.cld") if (directory/name).is_file()]
    main = [directory/name for name in ("main.cvd", "main.cld") if (directory/name).is_file()]
    return bool(daily and main and max(p.stat().st_mtime for p in daily) >= time.time()-48*3600)


def scan_file(path, deadline):
    if not database_ready():
        raise ProcessingFailure()
    result = run_tool([
        "clamscan", "--no-summary", "--max-filesize=10M", "--max-scansize=64M", "--max-files=1000",
        "--max-recursion=10", "--alert-exceeds-max=yes", "--alert-encrypted=yes",
        "--database=" + os.environ.get("CLAMAV_DATABASE_DIR", "/var/lib/clamav"), str(path),
    ], deadline, memory_mb=3072)
    if result.returncode == 1:
        raise ProcessingFailure("infected")
    if result.returncode != 0:
        raise ProcessingFailure()


def _reply(start_response, status, payload):
    raw = json.dumps(payload, ensure_ascii=False).encode()
    start_response(status, [("Content-Type", "application/json; charset=utf-8"),
                            ("Content-Length", str(len(raw))), ("Cache-Control", "no-store")])
    return [raw]


def application(environ, start_response):
    if environ.get("PATH_INFO") == "/healthz" and environ.get("REQUEST_METHOD") == "GET":
        ready = database_ready() and len(os.environ.get("DOCUMENT_PROCESSOR_TOKEN", "")) >= 32
        return _reply(start_response, "200 OK" if ready else "503 Service Unavailable", {"ready": ready})
    token = os.environ.get("DOCUMENT_PROCESSOR_TOKEN", "")
    if len(token) < 32:
        return _reply(start_response, "503 Service Unavailable", {"error": "unavailable"})
    if not hmac.compare_digest(environ.get("HTTP_AUTHORIZATION", "").encode(), ("Bearer " + token).encode()):
        return _reply(start_response, "403 Forbidden", {"error": "forbidden"})
    if environ.get("PATH_INFO") != "/process":
        return _reply(start_response, "404 Not Found", {"error": "not_found"})
    if environ.get("REQUEST_METHOD") != "POST":
        return _reply(start_response, "405 Method Not Allowed", {"error": "invalid_request"})
    extension = environ.get("HTTP_X_DOCUMENT_EXTENSION", "")
    mode = environ.get("HTTP_X_DOCUMENT_MODE", "")
    try:
        size = int(environ.get("CONTENT_LENGTH", "0"))
        if not 0 < size <= MAX_BYTES or extension not in MIME_TYPES or mode not in {"scan", "extract"}:
            raise ValueError()
        data = environ["wsgi.input"].read(size+1)
        if len(data) != size:
            raise ValueError()
    except (ValueError, KeyError, OSError):
        return _reply(start_response, "400 Bad Request", {"error": "invalid_file"})
    try:
        # No customer filename, no public storage, no persistent document cache.
        with tempfile.TemporaryDirectory(prefix="expiry-private-") as directory:
            path = Path(directory) / ("document" + extension)
            with path.open("wb") as output:
                os.chmod(path, 0o600)
                output.write(data)
            deadline = time.monotonic()+85
            scan_file(path, deadline)
            validate_signature(path)
            payload = {"scan": "clean", "sha256": hashlib.sha256(data).hexdigest(), "size": size}
            if mode == "extract":
                try:
                    text, warnings = read_document(path, deadline)
                    payload["fields"] = extract_fields(text, warnings)
                except ProcessingFailure:
                    payload["fields"] = {"name": None, "expiry_date": None, "notes": "", "evidence": "",
                                         "warnings": ["檔案已通過掃描，但無法自動辨識，請手動填寫。"],
                                         "method": "本機 OCR 與欄位規則擷取"}
            return _reply(start_response, "200 OK", payload)
    except ProcessingFailure as error:
        status = "422 Unprocessable Entity" if error.code in {"infected", "invalid_file"} else "503 Service Unavailable"
        return _reply(start_response, status, {"error": error.code})
    except Exception:
        # Never log document text, parser output, authentication headers or files.
        return _reply(start_response, "503 Service Unavailable", {"error": "unavailable"})
