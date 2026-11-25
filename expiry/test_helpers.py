"""Explicit fake scanner for unrelated regression tests, never production code."""
import hashlib
from unittest.mock import patch

from django.test import TestCase


class CleanScanTestCase(TestCase):
    def setUp(self):
        super().setUp()
        def clean(data, extension, extract):
            return {"scan": "clean", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        scanner = patch("expiry.document_processing._call_processor", side_effect=clean)
        scanner.start()
        self.addCleanup(scanner.stop)
