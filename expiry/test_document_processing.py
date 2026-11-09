import hashlib
import io
import json
import time
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.core import signing
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .document_processing import (DocumentProcessingError, ScanProof, make_scan_token, process_upload,
                                   read_upload, verify_scan_token)
from .models import ExpiryItem
from .storage import PrivateCloudinaryStorage

SERVICE_SETTINGS = {"DOCUMENT_PROCESSOR_URL": "http://processor:10000", "DOCUMENT_PROCESSOR_TOKEN": "test-service-token-not-for-production-1846"}
PDF = b"%PDF-1.4\nTest private document"


def uploaded(data=PDF):
    return SimpleUploadedFile("private.pdf", data, content_type="application/pdf")


def clean_response(data=PDF, **extra):
    payload = {"scan": "clean", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), **extra}
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 200
    response.iter_content.return_value = [json.dumps(payload).encode()]
    return response


@override_settings(**SERVICE_SETTINGS)
class DocumentProcessingClientTests(SimpleTestCase):
    def test_private_service_receives_bytes_and_no_customer_filename(self):
        file = uploaded()
        with patch("expiry.document_processing.requests.post", return_value=clean_response()) as post:
            result = process_upload(file)
            self.assertEqual(result["scan"], "clean")
            self.assertEqual(file.tell(), 0)
            self.assertEqual(post.call_args.args[0], "http://processor:10000/process")
            self.assertEqual(post.call_args.kwargs["data"], PDF)
            self.assertNotIn("private.pdf", str(post.call_args.kwargs["headers"]))
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
            process_upload(file)
            post.assert_called_once()  # Save can reuse only the same verified bytes.

    def test_changed_bytes_cannot_reuse_an_in_memory_scan(self):
        file = uploaded()
        file._document_scan_proof = ScanProof("not-the-actual-hash", len(PDF), time.time())
        with patch("expiry.document_processing.requests.post", return_value=clean_response()) as post:
            process_upload(file)
            post.assert_called_once()

    @override_settings(DOCUMENT_PROCESSOR_URL="", DOCUMENT_PROCESSOR_TOKEN="")
    def test_unconfigured_service_fails_closed(self):
        with self.assertRaises(DocumentProcessingError):
            process_upload(uploaded())

    @override_settings(DOCUMENT_PROCESSOR_URL="http://public.example.com")
    def test_public_service_must_use_https(self):
        with patch("expiry.document_processing.requests.post") as post:
            with self.assertRaises(DocumentProcessingError):
                process_upload(uploaded())
            post.assert_not_called()

    def test_network_failure_does_not_mark_a_file_clean(self):
        file = uploaded()
        with patch("expiry.document_processing.requests.post", side_effect=requests.Timeout("private credential diagnostic")):
            with self.assertRaises(DocumentProcessingError) as caught:
                process_upload(file)
        self.assertNotIn("diagnostic", str(caught.exception))
        self.assertFalse(hasattr(file, "_document_scan_proof"))

    def test_infection_and_unknown_results_are_rejected(self):
        for changes, status in [({"error": "infected"}, 422), ({"scan": "unknown"}, 200),
                                ({"sha256": "wrong"}, 200), ({"size": 999}, 200)]:
            with self.subTest(changes=changes):
                response = clean_response(**changes)
                response.status_code = status
                file = uploaded()
                with patch("expiry.document_processing.requests.post", return_value=response):
                    with self.assertRaises(DocumentProcessingError):
                        process_upload(file)
                self.assertFalse(hasattr(file, "_document_scan_proof"))

    def test_oversized_service_response_is_rejected(self):
        response = clean_response()
        response.iter_content.return_value = [b"x"*33000]
        with patch("expiry.document_processing.requests.post", return_value=response):
            with self.assertRaises(DocumentProcessingError):
                process_upload(uploaded())

    def test_file_size_and_extension_are_bounded_before_service_request(self):
        with patch("expiry.document_processing.requests.post") as post:
            for file in [uploaded(b""), uploaded(b"x"*(10*1024*1024+1)), SimpleUploadedFile("program.exe", b"MZ")]:
                with self.assertRaises(DocumentProcessingError):
                    process_upload(file)
            post.assert_not_called()

    def test_scan_receipt_is_bound_to_account_and_bytes(self):
        file = uploaded()
        file._document_scan_proof = ScanProof(hashlib.sha256(PDF).hexdigest(), len(PDF), time.time())
        token = make_scan_token(file, 1)
        self.assertTrue(verify_scan_token(uploaded(), 1, token))
        self.assertFalse(verify_scan_token(uploaded(), 2, token))
        self.assertFalse(verify_scan_token(uploaded(PDF+b"changed"), 1, token))
        self.assertFalse(verify_scan_token(uploaded(), 1, token+"tampered"))
        self.assertFalse(verify_scan_token(uploaded(), None, token))

    def test_expired_receipt_cannot_be_reused(self):
        file = uploaded()
        file._document_scan_proof = ScanProof(hashlib.sha256(PDF).hexdigest(), len(PDF), time.time())
        with patch("django.core.signing.time.time", return_value=time.time()-1000):
            token = make_scan_token(file, 1)
        self.assertFalse(verify_scan_token(uploaded(), 1, token))

    def test_direct_cloud_storage_upload_cannot_bypass_scan(self):
        with patch("expiry.document_processing.requests.post", side_effect=requests.ConnectionError()), patch("cloudinary.uploader.upload") as upload:
            with self.assertRaises(DocumentProcessingError):
                PrivateCloudinaryStorage().save("private.pdf", uploaded())
            upload.assert_not_called()


@override_settings(**SERVICE_SETTINGS)
class DocumentAnalysisFlowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("analysis-owner", password="Private-Analysis-671!")
        self.other = User.objects.create_user("analysis-other", password="Private-Analysis-672!")
        self.client.force_login(self.owner)
        self.url = reverse("analyze_document")
        self.fields = {"name": "Renewal policy", "expiry_date": "2027-06-01", "notes": "",
                       "evidence": "Expiry date: 2027-06-01", "warnings": []}

    def analyze(self):
        with patch("expiry.document_processing.requests.post", return_value=clean_response(fields=self.fields)):
            return self.client.post(self.url, {"document": uploaded()})

    def test_preview_is_private_and_does_not_save_a_file_or_item(self):
        with patch.object(default_storage, "save", side_effect=AssertionError("No storage before confirmation")):
            response = self.analyze()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["fields"]["expiry_date"], "2027-06-01")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertFalse(ExpiryItem.objects.exists())

    def test_customer_can_confirm_prefilled_data_and_save_without_another_scan(self):
        result = self.analyze().json()
        with patch("expiry.document_processing.requests.post", side_effect=AssertionError("Receipt should avoid reprocessing")):
            response = self.client.post(reverse("add_item"), {"name": "Customer corrected title", "expiry_date": "2027-06-02",
                "notes": "Checked by customer", "document": uploaded(), "scan_token": result["scan_token"]})
        self.assertEqual(response.status_code, 302)
        item = ExpiryItem.objects.get()
        self.assertEqual(item.user, self.owner)
        self.assertEqual(item.name, "Customer corrected title")
        self.assertEqual(item.expiry_date.isoformat(), "2027-06-02")

    def test_another_account_cannot_reuse_a_receipt(self):
        result = self.analyze().json()
        self.client.force_login(self.other)
        with patch("expiry.document_processing.requests.post", side_effect=requests.Timeout()) as post:
            response = self.client.post(reverse("add_item"), {"name": "Other", "expiry_date": "2027-06-01",
                "document": uploaded(), "scan_token": result["scan_token"]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(ExpiryItem.objects.exists())
        post.assert_called_once()

    def test_new_uploads_are_scanned_even_without_javascript_or_receipt(self):
        with patch("expiry.document_processing.requests.post", return_value=clean_response()) as post:
            response = self.client.post(reverse("add_item"), {"name": "Manual", "expiry_date": "2027-06-01", "document": uploaded()})
        self.assertEqual(response.status_code, 302)
        post.assert_called_once()

    def test_malware_is_not_stored_or_sent_to_ocr(self):
        response = clean_response(error="infected")
        response.status_code = 422
        with patch("expiry.document_processing.requests.post", return_value=response), patch.object(default_storage, "save") as save:
            result = self.client.post(self.url, {"document": uploaded()})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json()["error"], "infected")
        save.assert_not_called()
        self.assertFalse(ExpiryItem.objects.exists())

    def test_analysis_requires_login_post_and_csrf(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        anonymous = Client()
        self.assertEqual(anonymous.post(self.url, {"document": uploaded()}).status_code, 302)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.owner)
        self.assertEqual(csrf.post(self.url, {"document": uploaded()}).status_code, 403)

    def test_oversized_multipart_stream_is_stopped_before_processing(self):
        with patch("expiry.document_processing.requests.post") as post:
            response = self.client.post(self.url, {"document": uploaded(b"x"*(10*1024*1024+1))})
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()
        self.assertFalse(ExpiryItem.objects.exists())

    @override_settings(DOCUMENT_PROCESSOR_URL="", DOCUMENT_PROCESSOR_TOKEN="")
    def test_missing_processor_keeps_manual_items_available_and_rejects_new_files(self):
        response = self.client.post(reverse("add_item"), {"name": "Manual", "expiry_date": "2027-06-01"})
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("add_item"), {"name": "With file", "expiry_date": "2027-06-01", "document": uploaded()})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(ExpiryItem.objects.count(), 1)
