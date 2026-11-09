from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import DocumentDeletion, ExpiryItem
from .storage import DocumentStorageError, PRIVATE_PREFIX, PrivateCloudinaryStorage
from .test_helpers import CleanScanTestCase


class DocumentPrivacyTests(CleanScanTestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("private_owner", password="Private-Password-892!")
        self.other = User.objects.create_user("private_other", password="Private-Password-392!")
        self.client.force_login(self.owner)
        self.item = ExpiryItem.objects.create(user=self.owner, name="Private policy", expiry_date="2027-06-01", document=SimpleUploadedFile("policy.pdf", b"%PDF-1.4\nprivate", content_type="application/pdf"))
        self.url = reverse("download_document", args=[self.item.pk])

    def test_owner_downloads_through_application_without_exposing_storage_url(self):
        with patch.object(default_storage, "url", side_effect=AssertionError("Storage URLs must not be rendered")):
            self.assertEqual(self.item.document.url, self.url)
            self.assertContains(self.client.get(reverse("dashboard")), self.url)
            self.assertContains(self.client.get(reverse("edit_item", args=[self.item.pk])), self.url)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4\nprivate")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_anonymous_and_other_accounts_cannot_download(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.head(self.url).status_code, 404)

    def test_delete_removes_file_after_commit(self):
        name = self.item.document.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("delete_item", args=[self.item.pk]))
        self.assertFalse(default_storage.exists(name))
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertFalse(DocumentDeletion.objects.exists())

    def test_replacing_and_clearing_file_remove_previous_files(self):
        old = self.item.document.name
        url = reverse("edit_item", args=[self.item.pk])
        payload = {"name":"Private policy", "expiry_date":"2027-06-01"}
        replacement = SimpleUploadedFile("replacement.pdf", b"%PDF-1.4\nreplacement", content_type="application/pdf")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {**payload, "document":replacement})
        self.item.refresh_from_db()
        current = self.item.document.name
        self.assertFalse(default_storage.exists(old))
        self.assertTrue(default_storage.exists(current))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {**payload, "document-clear":"on"})
        self.assertFalse(default_storage.exists(current))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_rolled_back_delete_keeps_the_document(self):
        pk, name = self.item.pk, self.item.document.name
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    self.item.delete()
                    raise ValueError("Rollback")
        self.assertTrue(ExpiryItem.objects.filter(pk=pk).exists())
        self.assertTrue(default_storage.exists(name))
        self.assertFalse(DocumentDeletion.objects.exists())

    def test_failed_storage_deletion_is_durable_and_retryable(self):
        name = self.item.document.name
        with patch.object(default_storage, "delete", side_effect=OSError("storage unavailable")), self.captureOnCommitCallbacks(execute=True):
            self.item.delete()
        job = DocumentDeletion.objects.get()
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.last_error, "OSError")
        self.assertTrue(default_storage.exists(name))
        job.retry_at = timezone.now()-timedelta(seconds=1)
        job.save(update_fields=["retry_at"])
        call_command("cleanup_documents", stdout=StringIO())
        self.assertFalse(default_storage.exists(name))
        self.assertFalse(DocumentDeletion.objects.exists())

    def test_user_cascade_deletion_also_cleans_file(self):
        name = self.item.document.name
        with self.captureOnCommitCallbacks(execute=True):
            self.owner.delete()
        self.assertFalse(default_storage.exists(name))

    def test_legacy_migration_updates_references_and_preserves_unreferenced_files(self):
        ExpiryItem.objects.filter(pk=self.item.pk).update(document="media/documents/legacy.pdf")
        fake = MagicMock()
        fake.secure_existing.side_effect = lambda name: PRIVATE_PREFIX + name.rsplit("/",1)[-1]
        fake.legacy_public_names.return_value = ["media/documents/old-orphan.pdf"]
        with patch("expiry.management.commands.secure_existing_documents.default_storage", fake):
            call_command("secure_existing_documents", stdout=StringIO())
        self.item.refresh_from_db()
        self.assertEqual(self.item.document.name, PRIVATE_PREFIX+"legacy.pdf")
        fake.secure_existing.assert_any_call("media/documents/old-orphan.pdf")
        fake.delete.assert_not_called()
        self.assertFalse(DocumentDeletion.objects.exists())

    def test_legacy_migration_dry_run_does_not_change_files_or_rows(self):
        previous = self.item.document.name
        fake = MagicMock()
        fake.legacy_public_names.return_value = ["media/documents/legacy.pdf"]
        with patch("expiry.management.commands.secure_existing_documents.default_storage", fake):
            call_command("secure_existing_documents", dry_run=True, stdout=StringIO())
        self.item.refresh_from_db()
        self.assertEqual(self.item.document.name, previous)
        fake.secure_existing.assert_not_called()


@override_settings(CLOUDINARY_STORAGE={"CLOUD_NAME":"test-cloud", "API_KEY":"test-key", "API_SECRET":"test-secret"})
class PrivateCloudinaryTests(CleanScanTestCase):
    def test_new_uploads_use_authenticated_delivery_and_opaque_names(self):
        storage = PrivateCloudinaryStorage()
        with patch("expiry.storage.cloudinary.uploader.upload", side_effect=lambda content, **kwargs:{"public_id":kwargs["public_id"], "type":"authenticated"}) as upload:
            name = storage.save("documents/customer-confidential.pdf", SimpleUploadedFile("customer-confidential.pdf", b"%PDF-1.4"))
        self.assertTrue(name.startswith(PRIVATE_PREFIX))
        self.assertNotIn("customer-confidential", name)
        self.assertEqual(upload.call_args.kwargs["type"], "authenticated")
        self.assertEqual(upload.call_args.kwargs["resource_type"], "raw")
        with self.assertRaises(ValueError):
            storage.url(name)

    def test_legacy_public_files_have_no_download_fallback(self):
        with patch("expiry.storage.requests.get") as get:
            with self.assertRaises(DocumentStorageError):
                PrivateCloudinaryStorage().open("media/documents/legacy.pdf")
            get.assert_not_called()

    def test_deletion_requests_provider_invalidation(self):
        storage = PrivateCloudinaryStorage()
        with patch("expiry.storage.cloudinary.uploader.destroy", return_value={"result":"ok"}) as destroy:
            storage.delete(PRIVATE_PREFIX+"example.pdf")
        self.assertEqual(destroy.call_args.kwargs["type"], "authenticated")
        self.assertTrue(destroy.call_args.kwargs["invalidate"])

    def test_storage_rejects_provider_response_with_public_delivery_type(self):
        with patch("expiry.storage.cloudinary.uploader.upload", side_effect=lambda content, **kwargs:{"public_id":kwargs["public_id"], "type":"upload"}):
            with self.assertRaises(DocumentStorageError):
                PrivateCloudinaryStorage().save("private.pdf", SimpleUploadedFile("private.pdf", b"test"))

    def test_retry_of_legacy_migration_recovers_existing_private_destination(self):
        with patch("expiry.storage.cloudinary.api.resource", return_value={}), patch("expiry.storage.cloudinary.uploader.rename") as rename:
            name = PrivateCloudinaryStorage().secure_existing("media/documents/legacy.pdf")
        self.assertTrue(name.startswith(PRIVATE_PREFIX+"legacy-"))
        rename.assert_not_called()
