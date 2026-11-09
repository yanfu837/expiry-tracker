import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import application as service
from .extraction import ProcessingFailure, _office_text, extract_fields, run_tool


class PrivateServiceTests(unittest.TestCase):
    def request(self, body=b"private text", token=None, **overrides):
        environ = {"PATH_INFO": "/process", "REQUEST_METHOD": "POST", "CONTENT_LENGTH": str(len(body)),
                   "wsgi.input": io.BytesIO(body), "HTTP_AUTHORIZATION": "Bearer " + (token or "test-only-private-service-key-1764"),
                   "HTTP_X_DOCUMENT_EXTENSION": ".txt", "HTTP_X_DOCUMENT_MODE": "extract", **overrides}
        status = []
        with patch.dict(os.environ, {"DOCUMENT_PROCESSOR_TOKEN": "test-only-private-service-key-1764"}):
            result = service.application(environ, lambda code, headers: status.append(code))
        return status[0], json.loads(b"".join(result))

    def test_scan_precedes_parsing_and_temporary_files_are_removed(self):
        order = []
        paths = []
        def scan(path, deadline):
            order.append("scan")
            paths.append(path)
            self.assertEqual(path.read_bytes(), b"private text")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        def read(path, deadline):
            self.assertEqual(order, ["scan"])
            order.append("read")
            return "Document title: License\nExpiry date: 2027-06-01", []
        with patch.object(service, "scan_file", side_effect=scan), patch.object(service, "read_document", side_effect=read):
            status, result = self.request()
        self.assertEqual(status, "200 OK")
        self.assertEqual(result["fields"]["expiry_date"], "2027-06-01")
        self.assertEqual(order, ["scan", "read"])
        self.assertFalse(paths[0].exists())

    def test_infected_file_never_reaches_ocr_and_is_removed(self):
        paths = []
        def scan(path, deadline):
            paths.append(path)
            raise ProcessingFailure("infected")
        with patch.object(service, "scan_file", side_effect=scan), patch.object(service, "read_document") as read:
            status, result = self.request()
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertEqual(result, {"error": "infected"})
        read.assert_not_called()
        self.assertFalse(paths[0].exists())

    def test_bad_authentication_does_not_read_or_write_the_file(self):
        with patch.object(service, "scan_file") as scan, patch.object(service.tempfile, "TemporaryDirectory") as temp:
            status, result = self.request(token="invalid")
        self.assertEqual(status, "403 Forbidden")
        scan.assert_not_called()
        temp.assert_not_called()

    def test_clean_but_unreadable_document_allows_manual_entry(self):
        with patch.object(service, "scan_file"), patch.object(service, "read_document", side_effect=ProcessingFailure("unsupported")):
            status, result = self.request()
        self.assertEqual(status, "200 OK")
        self.assertEqual(result["scan"], "clean")
        self.assertIsNone(result["fields"]["expiry_date"])
        self.assertTrue(result["fields"]["warnings"])

    def test_scan_only_does_not_run_ocr(self):
        with patch.object(service, "scan_file"), patch.object(service, "read_document") as read:
            status, result = self.request(HTTP_X_DOCUMENT_MODE="scan")
        self.assertEqual(status, "200 OK")
        self.assertNotIn("fields", result)
        read.assert_not_called()

    def test_renamed_executable_is_rejected_before_ocr(self):
        with patch.object(service, "scan_file"), patch.object(service, "read_document") as read:
            status, result = self.request(body=b"MZ executable", HTTP_X_DOCUMENT_EXTENSION=".pdf")
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertEqual(result, {"error": "invalid_file"})
        read.assert_not_called()

    def test_stale_signature_database_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CLAMAV_DATABASE_DIR": directory}):
            main = Path(directory)/"main.cvd"
            daily = Path(directory)/"daily.cld"
            main.write_bytes(b"test fixture")
            daily.write_bytes(b"test fixture")
            self.assertTrue(service.database_ready())
            os.utime(daily, (time.time()-49*3600,)*2)
            self.assertFalse(service.database_ready())

    def test_scanner_error_is_not_treated_as_clean(self):
        with patch.object(service, "database_ready", return_value=True):
            for code in [1, 2, -9]:
                with self.subTest(code=code), patch.object(service, "run_tool", return_value=SimpleNamespace(returncode=code)):
                    with self.assertRaises(ProcessingFailure):
                        service.scan_file(Path("test.pdf"), time.monotonic()+60)


class LocalFieldExtractionTests(unittest.TestCase):
    def test_labelled_expiry_and_roc_conversion(self):
        result = extract_fields("文件名稱：公司責任保險\n簽訂日期：2025/03/20\n有效期限：民國116年3月20日\n備註：到期前辦理更新")
        self.assertEqual(result["name"], "公司責任保險")
        self.assertEqual(result["expiry_date"], "2027-03-20")
        self.assertEqual(result["notes"], "到期前辦理更新")

    def test_unlabelled_or_issue_dates_are_not_guessed_as_expiry(self):
        for text in ["Insurance certificate\n2027/06/01", "Issued on: 2027/06/01", "Expiry date: 2027/02/30"]:
            self.assertIsNone(extract_fields(text)["expiry_date"])

    def test_conflicting_expiry_dates_require_customer_review(self):
        result = extract_fields("Expiry date: 2027/06/01\n到期日：2028/06/01")
        self.assertIsNone(result["expiry_date"])
        self.assertTrue(result["warnings"])

    def test_following_issue_date_does_not_change_a_clear_expiry(self):
        result = extract_fields("Expiry date: 2027/06/01\nIssue date: 2025/06/01")
        self.assertEqual(result["expiry_date"], "2027-06-01")

    def test_expiry_heading_does_not_steal_the_following_issue_date(self):
        self.assertIsNone(extract_fields("Expiry information\nIssue date: 2025/06/01")["expiry_date"])
        self.assertEqual(extract_fields("到期日\n2027/06/01")["expiry_date"], "2027-06-01")

    def test_cjk_ocr_spacing_is_normalized(self):
        result = extract_fields("公 司 保 險\n有 效 期 限：民 國 116年3月20日")
        self.assertEqual(result["expiry_date"], "2027-03-20")

    def test_explicit_period_uses_the_end_date(self):
        result = extract_fields("保險期間：2026/06/01 至 2027/06/01")
        self.assertEqual(result["expiry_date"], "2027-06-01")

    def test_csv_and_spreadsheet_headers_are_used_for_the_corresponding_values(self):
        for separator in [",", "\t"]:
            with self.subTest(separator=separator):
                text = separator.join(["Name", "Expiry date", "Notes"])+"\n"+separator.join(["License", "2027/06/01", "Renew soon"])
                result = extract_fields(text)
                self.assertEqual(result["name"], "License")
                self.assertEqual(result["expiry_date"], "2027-06-01")
                self.assertEqual(result["notes"], "Renew soon")

    def test_multiple_table_expiries_are_not_silently_merged(self):
        result = extract_fields("Name,Expiry date\nLicense,2027/06/01\nContract,2028/06/01")
        self.assertIsNone(result["expiry_date"])
        self.assertTrue(result["warnings"])

    def test_chinese_spreadsheet_headers_keep_column_boundaries(self):
        result = extract_fields("名稱\t到期日\t備註\n公司責任保險\t2027/06/01\t請更新")
        self.assertEqual(result["name"], "公司責任保險")
        self.assertEqual(result["expiry_date"], "2027-06-01")
        self.assertEqual(result["notes"], "請更新")

    def test_docx_is_read_without_running_office_or_macros(self):
        xml = '<w:document xmlns:w="urn:test"><w:p><w:r><w:t>文件名稱：責任保險</w:t></w:r></w:p><w:p><w:r><w:t>到期日：2027/06/01</w:t></w:r></w:p></w:document>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"test.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", xml)
            self.assertEqual(extract_fields(_office_text(path))["expiry_date"], "2027-06-01")

    def test_xlsx_resolves_shared_strings_and_excel_date_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"test.xlsx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("xl/sharedStrings.xml", '<sst><si><t>Expiry date</t></si></sst>')
                archive.writestr("xl/styles.xml", '<styleSheet><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
                archive.writestr("xl/worksheets/sheet1.xml", '<worksheet><row><c t="s"><v>0</v></c><c s="1"><v>46539</v></c></row></worksheet>')
            self.assertEqual(extract_fields(_office_text(path))["expiry_date"], "2027-06-01")

    def test_xml_entities_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"test.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", '<!DOCTYPE a [<!ENTITY secret SYSTEM "file:///etc/passwd">]><a>&secret;</a>')
            with self.assertRaises(ProcessingFailure):
                _office_text(path)

    @unittest.skipUnless(sys.platform == "linux", "Service sandbox runs in Linux containers")
    def test_parser_process_cannot_open_network_sockets(self):
        script = "import socket,sys\ntry: socket.socket()\nexcept PermissionError: sys.exit(0)\nsys.exit(1)"
        result = run_tool([sys.executable, "-c", script], time.monotonic()+15)
        self.assertEqual(result.returncode, 0)
