import hashlib
import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core import mail
from django.core import signing
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .document_processing import DocumentProcessingError
from .models import DocumentImportBatch, DocumentImportCandidate, ExpiryItem, ReminderDelivery
from .reminders import send_reminders


@override_settings(DOCUMENT_PROCESSOR_URL="http://processor:10000", DOCUMENT_PROCESSOR_TOKEN="batch-tests-only-service-key-123456")
class BatchImportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("batch-owner", email="owner@example.com", password="Batch-Private-427!")
        self.other = User.objects.create_user("batch-other", email="other@example.com", password="Batch-Private-428!")
        self.client.force_login(self.owner)
        self.date = timezone.localdate()+timedelta(days=7)
        self.data = b"%PDF-1.4\nPrivate sample"
        self.fail_once = False
        def process(data, extension, extract):
            if b"infected" in data:
                raise DocumentProcessingError("infected")
            if self.fail_once:
                self.fail_once = False
                raise DocumentProcessingError("unavailable")
            uncertain = b"uncertain" in data
            return {"scan": "clean", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                    "fields": {"name": "Suggested policy", "expiry_date": None if uncertain else self.date.isoformat(),
                               "notes": "Renew soon", "evidence": "到期日 2027/06/01；另一到期日 2028/06/01" if uncertain else "到期日 "+self.date.isoformat(),
                               "warnings": ["日期不明或有衝突，請確認。"] if uncertain else []}}
        scanner = patch("expiry.document_processing._call_processor", side_effect=process)
        self.scanner = scanner.start()
        self.addCleanup(scanner.stop)

    def create_batch(self, files=None):
        files = files or [("policy.pdf", self.data)]
        payload = {"files": [{"key": str(uuid4()), "name": name, "size": len(data)} for name, data in files]}
        response = self.client.post(reverse("batch_create"), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def analyze(self, candidate, data=None):
        return self.client.post(candidate["urls"]["analyze"], {"document": SimpleUploadedFile(candidate["filename"], data or self.data, content_type="application/pdf")})

    def save(self, candidate, **changes):
        payload = {"confirmed": "true", "name": "Confirmed policy", "expiry_date": self.date.isoformat(), "notes": "Checked",
                   "document": SimpleUploadedFile(candidate["filename"], self.data, content_type="application/pdf"), **changes}
        return self.client.post(candidate["urls"]["save"], payload)

    def ready(self, data=None):
        batch = self.create_batch([("policy.pdf", data or self.data)])
        row = batch["candidates"][0]
        response = self.analyze(row, data)
        self.assertEqual(response.status_code, 200, response.content)
        return batch, response.json()["candidate"]

    def test_file_count_and_total_size_limits_are_checked_before_creating_drafts(self):
        def metadata(count, size):
            return {"files": [{"key": str(uuid4()), "name": f"doc-{i}.pdf", "size": size} for i in range(count)]}
        cases = [(11, 1, 400), (10, 5*1024*1024, 201), (6, 9*1024*1024, 400),
                 (1, 10*1024*1024, 201), (1, 10*1024*1024+1, 400)]
        for count, size, expected in cases:
            with self.subTest(count=count, size=size):
                before = DocumentImportBatch.objects.count()
                result = self.client.post(reverse("batch_create"), data=json.dumps(metadata(count, size)), content_type="application/json")
                self.assertEqual(result.status_code, expected)
                self.assertEqual(DocumentImportBatch.objects.count(), before+(expected == 201))
        self.scanner.assert_not_called()
        self.assertFalse(ExpiryItem.objects.exists())

    def test_bad_format_is_one_failed_row_and_does_not_block_valid_files(self):
        batch = self.create_batch([("policy.pdf", self.data), ("program.exe", b"MZ")])
        self.assertEqual(batch["candidates"][1]["status"], "failed")
        self.assertEqual(self.analyze(batch["candidates"][0]).status_code, 200)

    def test_preview_stays_separate_from_items_storage_and_reminders(self):
        with patch.object(default_storage, "save") as storage:
            batch, row = self.ready()
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["expiry_date"], self.date.isoformat())
        self.assertFalse(ExpiryItem.objects.exists())
        storage.assert_not_called()
        self.assertEqual(send_reminders()["sent"], 0)
        self.assertFalse(ReminderDelivery.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_confirmation_is_required_and_date_cannot_be_left_blank(self):
        batch, row = self.ready()
        self.assertEqual(self.save(row, confirmed="false").status_code, 400)
        self.assertEqual(self.save(row, expiry_date="").status_code, 400)
        self.assertFalse(ExpiryItem.objects.exists())

    def test_uncertain_date_stays_blank_with_original_evidence_for_manual_review(self):
        raw = self.data+b" uncertain"
        batch, row = self.ready(raw)
        self.assertIsNone(row["expiry_date"])
        self.assertIn("2027/06/01", row["evidence"])
        self.assertIn("2028/06/01", row["evidence"])
        result = self.save(row, expiry_date="", document=SimpleUploadedFile("policy.pdf", raw, content_type="application/pdf"))
        self.assertEqual(result.status_code, 400)
        self.assertFalse(ExpiryItem.objects.exists())
        result = self.save(row, document=SimpleUploadedFile("policy.pdf", raw, content_type="application/pdf"))
        self.assertEqual(result.status_code, 201)

    def test_confirmed_item_uses_customer_edits_and_can_receive_reminders(self):
        batch, row = self.ready()
        response = self.save(row, name="Customer corrected name", notes="Customer checked original")
        self.assertEqual(response.status_code, 201)
        item = ExpiryItem.objects.get()
        self.assertEqual(item.name, "Customer corrected name")
        self.assertEqual(item.notes, "Customer checked original")
        self.assertEqual(item.user, self.owner)
        self.assertEqual(send_reminders()["sent"], 1)

    def test_lost_response_retry_returns_original_item_without_new_scan_or_upload(self):
        batch, row = self.ready()
        first = self.save(row)
        self.assertEqual(first.status_code, 201)
        item = ExpiryItem.objects.get()
        self.scanner.reset_mock()
        with patch.object(default_storage, "save") as storage:
            retried = self.client.post(row["urls"]["save"], {"confirmed": "true", "name": "Different retry"})
        self.assertEqual(retried.status_code, 200)
        self.assertTrue(retried.json()["already_saved"])
        self.assertEqual(ExpiryItem.objects.get().pk, item.pk)
        self.assertEqual(ExpiryItem.objects.get().name, "Confirmed policy")
        self.scanner.assert_not_called()
        storage.assert_not_called()

    def test_failed_file_can_be_retried_without_touching_other_successful_files(self):
        batch = self.create_batch([("first.pdf", self.data), ("second.pdf", self.data)])
        first, second = batch["candidates"]
        self.fail_once = True
        self.assertEqual(self.analyze(first).status_code, 503)
        self.assertEqual(self.analyze(second).status_code, 200)
        self.assertEqual(self.save(second).status_code, 201)
        self.assertEqual(self.analyze(first).status_code, 200)
        self.assertEqual(self.save(first).status_code, 201)
        self.assertEqual(self.save(second).status_code, 200)
        self.assertEqual(ExpiryItem.objects.count(), 2)

    def test_storage_failure_does_not_prevent_other_rows_or_create_duplicate_on_retry(self):
        batch = self.create_batch([("first.pdf", self.data), ("second.pdf", self.data)])
        first, second = batch["candidates"]
        self.analyze(first); self.analyze(second)
        with patch.object(default_storage, "save", side_effect=OSError("private provider diagnostic")):
            self.assertEqual(self.save(first).status_code, 503)
        self.assertEqual(self.save(second).status_code, 201)
        self.assertEqual(self.save(first).status_code, 201)
        self.assertEqual(self.save(first).status_code, 200)
        self.assertEqual(ExpiryItem.objects.count(), 2)

    def test_inflight_save_is_not_duplicated_and_expired_lease_can_be_recovered(self):
        batch, row = self.ready()
        stored = DocumentImportCandidate.objects.get(pk=row["id"])
        stored.status, stored.lease_token, stored.lease_until = "saving", uuid4(), timezone.now()+timedelta(minutes=1)
        stored.save()
        with patch.object(default_storage, "save") as storage:
            self.assertEqual(self.save(row).status_code, 409)
        storage.assert_not_called()
        stored.lease_until = timezone.now()-timedelta(seconds=1); stored.save()
        self.assertEqual(self.save(row).status_code, 201)
        self.assertEqual(self.save(row).status_code, 200)
        self.assertEqual(ExpiryItem.objects.count(), 1)

    def test_deleted_saved_item_is_not_recreated_by_retry(self):
        batch, row = self.ready()
        self.assertEqual(self.save(row).status_code, 201)
        ExpiryItem.objects.get().delete()
        result = self.save(row)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["candidate"]["saved"])
        self.assertIsNone(result.json()["candidate"]["item_url"])
        self.assertFalse(ExpiryItem.objects.exists())

    def test_same_document_can_supply_two_distinct_expiry_matters(self):
        batch, first = self.ready()
        result = self.client.post(first["urls"]["duplicate"])
        self.assertEqual(result.status_code, 201)
        second = result.json()["candidate"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["source_key"], second["source_key"])
        self.assertIsNone(second["expiry_date"])
        self.assertEqual(self.save(first).status_code, 201)
        self.assertEqual(self.save(second, name="Another matter", expiry_date=(self.date+timedelta(days=10)).isoformat()).status_code, 201)
        self.assertEqual(ExpiryItem.objects.count(), 2)

    def test_skip_never_creates_a_reminder_item(self):
        batch, row = self.ready()
        self.assertEqual(self.client.post(row["urls"]["skip"]).status_code, 200)
        self.assertEqual(self.save(row).status_code, 409)
        self.assertFalse(ExpiryItem.objects.exists())
        self.assertEqual(send_reminders()["sent"], 0)
        self.assertEqual(self.analyze(row).json()["candidate"]["status"], "ready")
        self.assertFalse(ExpiryItem.objects.exists())

    def test_expired_unconfirmed_batch_cannot_be_processed_or_saved(self):
        batch, row = self.ready()
        DocumentImportBatch.objects.filter(pk=batch["id"]).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.save(row).status_code, 410)
        self.assertEqual(self.analyze(row).status_code, 410)
        self.assertEqual(self.client.post(row["urls"]["skip"]).status_code, 410)
        self.assertEqual(self.client.post(row["urls"]["duplicate"]).status_code, 410)
        self.assertFalse(ExpiryItem.objects.exists())

    def test_stale_receipt_forces_a_fresh_scan_before_saving(self):
        batch, row = self.ready()
        stored = DocumentImportCandidate.objects.get(pk=row["id"])
        payload = signing.loads(stored.scan_token, salt="expiry.document-scan.v1")
        signer = signing.TimestampSigner(salt="expiry.document-scan.v1")
        old_timestamp = signing.b62_encode(int(timezone.now().timestamp())-3600)
        with patch.object(signer, "timestamp", return_value=old_timestamp):
            stored.scan_token = signer.sign_object(payload)
        stored.save(update_fields=["scan_token"])
        self.scanner.reset_mock()
        self.assertEqual(self.save(row).status_code, 201)
        self.scanner.assert_called_once()
        self.assertFalse(self.scanner.call_args.args[2])  # Scan-only; do not overwrite reviewed fields.

    def test_same_size_changed_file_requires_a_new_batch(self):
        batch, row = self.ready()
        self.scanner.reset_mock()
        changed = self.data.replace(b"Private", b"Changed")
        self.assertEqual(len(changed), len(self.data))
        self.assertEqual(self.analyze(row, changed).status_code, 409)
        self.scanner.assert_not_called()

    def test_changed_bytes_cannot_be_saved_with_old_analysis(self):
        batch, row = self.ready()
        changed = SimpleUploadedFile("policy.pdf", self.data+b"changed", content_type="application/pdf")
        self.assertEqual(self.save(row, document=changed).status_code, 409)
        self.assertFalse(ExpiryItem.objects.exists())

    def test_actual_file_size_must_match_the_batch_manifest(self):
        batch = self.create_batch()
        self.scanner.reset_mock()
        self.assertEqual(self.analyze(batch["candidates"][0], self.data+b"extra").status_code, 400)
        self.scanner.assert_not_called()

    def test_other_accounts_cannot_read_process_confirm_skip_or_duplicate(self):
        batch, row = self.ready()
        self.client.force_login(self.other)
        self.scanner.reset_mock()
        for route in [batch["url"], batch["status_url"]]:
            self.assertEqual(self.client.get(route).status_code, 404)
        for route in row["urls"].values():
            self.assertEqual(self.client.post(route).status_code, 404)
        self.scanner.assert_not_called()
        self.assertFalse(ExpiryItem.objects.exists())

    def test_authentication_and_csrf_are_required(self):
        anon = Client()
        self.assertEqual(anon.get(reverse("batch_import")).status_code, 302)
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.owner)
        self.assertEqual(csrf.post(reverse("batch_create"), "{}", content_type="application/json").status_code, 403)
        self.assertEqual(self.client.get(reverse("batch_create")).status_code, 405)

    def test_expired_drafts_cannot_be_confirmed_and_cleanup_preserves_saved_items(self):
        batch, row = self.ready()
        self.assertEqual(self.save(row).status_code, 201)
        DocumentImportBatch.objects.filter(pk=batch["id"]).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertTrue(self.client.get(batch["status_url"]).json()["expired"])
        self.assertEqual(self.save(row).status_code, 200)  # Saved tombstone still handles retries.
        call_command("cleanup_import_batches", stdout=StringIO())
        self.assertFalse(DocumentImportBatch.objects.exists())
        self.assertEqual(ExpiryItem.objects.count(), 1)
        self.assertEqual(self.save(row).status_code, 404)

    def test_cleanup_leaves_active_leases_until_they_expire(self):
        batch = self.create_batch()
        DocumentImportBatch.objects.filter(pk=batch["id"]).update(expires_at=timezone.now()-timedelta(seconds=1))
        DocumentImportCandidate.objects.filter(batch_id=batch["id"]).update(status="processing", lease_until=timezone.now()+timedelta(minutes=1))
        call_command("cleanup_import_batches", stdout=StringIO())
        self.assertTrue(DocumentImportBatch.objects.exists())
        DocumentImportCandidate.objects.update(lease_until=timezone.now()-timedelta(seconds=1))
        call_command("cleanup_import_batches", stdout=StringIO())
        self.assertFalse(DocumentImportBatch.objects.exists())

    def test_batch_page_has_limits_pdf_range_and_manual_fallback(self):
        page = self.client.get(reverse("batch_import"))
        self.assertContains(page, "50 MB")
        self.assertContains(page, "first 10 pages")
        self.assertContains(page, "Create an item without a document")

    def test_db_failure_discards_uncommitted_file_and_allows_retry(self):
        batch, row = self.ready()
        names = []
        original = default_storage.save
        def store(*args, **kwargs):
            name = original(*args, **kwargs); names.append(name); return name
        with self.captureOnCommitCallbacks(execute=True), patch.object(default_storage, "save", side_effect=store), patch.object(ExpiryItem, "save", side_effect=RuntimeError("DB interrupted")):
            self.assertEqual(self.save(row).status_code, 503)
        self.assertFalse(ExpiryItem.objects.exists())
        self.assertTrue(names)
        self.assertFalse(default_storage.exists(names[0]))
        self.assertEqual(self.save(row).status_code, 201)
