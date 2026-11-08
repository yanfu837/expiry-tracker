import hashlib
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from uuid import uuid4

import cloudinary
import cloudinary.api
import cloudinary.uploader
import requests
from cloudinary.utils import cloudinary_url
from django.conf import settings
from django.core.files import File
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible

PRIVATE_PREFIX = "expiry-tracker-private/"
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
LEGACY_PREFIX = "media/documents/"


class DocumentStorageError(OSError):
    pass


@deconstructible
class PrivateFileSystemStorage(FileSystemStorage):
    def url(self, name):
        raise ValueError("Use the protected document download view.")

    def _save(self, name, content):
        return super()._save(PRIVATE_PREFIX + uuid4().hex + PurePosixPath(name).suffix.lower(), content)


@deconstructible
class PrivateCloudinaryStorage(Storage):
    def __init__(self):
        credentials = settings.CLOUDINARY_STORAGE
        options = {"secure": True}
        for key, option in [("CLOUD_NAME", "cloud_name"), ("API_KEY", "api_key"), ("API_SECRET", "api_secret")]:
            if credentials.get(key):
                options[option] = credentials[key]
        cloudinary.config(**options)

    def url(self, name):
        raise ValueError("Use the protected document download view.")

    def get_available_name(self, name, max_length=None):
        return name

    def _save(self, name, content):
        public_id = PRIVATE_PREFIX + uuid4().hex + PurePosixPath(name).suffix.lower()
        try:
            response = cloudinary.uploader.upload(
                content, public_id=public_id, resource_type="raw", type="authenticated",
                overwrite=False, tags=["expiry-tracker-private"], timeout=20,
            )
        except Exception:
            raise DocumentStorageError("Private document upload failed.") from None
        if response.get("public_id") != public_id or response.get("type") != "authenticated":
            raise DocumentStorageError("Private document storage was not confirmed.")
        return public_id

    def _open(self, name, mode="rb"):
        if not name.startswith(PRIVATE_PREFIX) or mode != "rb":
            raise DocumentStorageError("Document is not available in private storage.")
        url, _ = cloudinary_url(name, resource_type="raw", type="authenticated", secure=True, sign_url=True)
        output = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
        try:
            with requests.get(url, stream=True, timeout=(5, 20), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise DocumentStorageError("Private document could not be read.")
                total = 0
                for chunk in response.iter_content(64 * 1024):
                    total += len(chunk)
                    if total > MAX_DOCUMENT_BYTES:
                        raise DocumentStorageError("Document exceeds the download size limit.")
                    output.write(chunk)
            output.seek(0)
            return File(output, name=PurePosixPath(name).name)
        except Exception:
            output.close()
            raise DocumentStorageError("Private document download failed.") from None

    def delete(self, name):
        self.delete_asset(name, "authenticated" if name.startswith(PRIVATE_PREFIX) else "upload")

    def delete_asset(self, name, delivery_type):
        try:
            result = cloudinary.uploader.destroy(name, resource_type="raw", type=delivery_type, invalidate=True, timeout=10)
        except Exception:
            raise DocumentStorageError("Document deletion failed.") from None
        if result.get("result") not in {"ok", "not found"}:
            raise DocumentStorageError("Document deletion was not confirmed.")

    def secure_existing(self, name):
        if name.startswith(PRIVATE_PREFIX):
            return name
        target = PRIVATE_PREFIX + "legacy-" + hashlib.sha256(name.encode()).hexdigest()[:32] + PurePosixPath(name).suffix.lower()
        # Stable destination makes retries safe if renaming succeeded before a DB update failed.
        try:
            cloudinary.api.resource(target, resource_type="raw", type="authenticated", timeout=10)
            return target
        except cloudinary.api.NotFound:
            pass
        except Exception:
            raise DocumentStorageError("Private document lookup failed.") from None
        try:
            result = cloudinary.uploader.rename(name, target, resource_type="raw", type="upload", to_type="authenticated", overwrite=False, invalidate=True, timeout=20)
        except Exception:
            raise DocumentStorageError("Existing document could not be secured.") from None
        if result.get("public_id") != target:
            raise DocumentStorageError("Document migration was not confirmed.")
        return target

    def legacy_public_names(self):
        cursor = None
        while True:
            options = {"resource_type": "raw", "type": "upload", "prefix": LEGACY_PREFIX, "tags": True, "max_results": 500, "timeout": 20}
            if cursor:
                options["next_cursor"] = cursor
            try:
                response = cloudinary.api.resources(**options)
            except Exception:
                raise DocumentStorageError("Legacy document inventory is unavailable.") from None
            for resource in response.get("resources", []):
                if "media" in resource.get("tags", []) and resource["public_id"].startswith(LEGACY_PREFIX):
                    yield resource["public_id"]
            cursor = response.get("next_cursor")
            if not cursor:
                break
