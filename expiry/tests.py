import re
from datetime import date, timedelta
from io import StringIO
from smtplib import SMTPException
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .forms import ExpiryItemForm
from .models import ExpiryItem
from .test_helpers import CleanScanTestCase

PASSWORD = "River-Business-9382!"


class AuthTests(TestCase):
    def test_registration_saves_recovery_email_and_logs_in(self):
        response = self.client.post(reverse("register"), {
            "username": "newowner", "email": "owner@example.com",
            "password1": PASSWORD, "password2": PASSWORD,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="newowner")
        self.assertEqual(user.email, "owner@example.com")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_registration_rejects_weak_password_and_missing_email(self):
        response = self.client.post(reverse("register"), {
            "username": "newowner", "password1": "12345678", "password2": "12345678",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(User.objects.exists())

    def test_login_and_invalid_credentials(self):
        User.objects.create_user("owner", password=PASSWORD)
        invalid = self.client.post(reverse("login"), {"username": "owner", "password": "incorrect"})
        self.assertEqual(invalid.status_code, 200)
        self.assertTrue(invalid.context["form"].non_field_errors())
        self.assertRedirects(self.client.post(reverse("login"), {"username": "owner", "password": PASSWORD}), reverse("dashboard"))

    def test_private_pages_require_login(self):
        for url in [reverse("dashboard"), reverse("add_item"), reverse("account_email"), reverse("edit_item", args=[1]), reverse("delete_item", args=[1])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 302)
                self.assertEqual(self.client.post(url, {}).status_code, 302)

    def test_login_requires_csrf_token(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse("login"), {"username": "owner", "password": PASSWORD}).status_code, 403)

    def test_existing_user_can_save_email_only_with_current_password(self):
        user = User.objects.create_user("owner", password=PASSWORD)
        self.client.force_login(user)
        response = self.client.post(reverse("account_email"), {"email": "owner@example.com", "current_password": "wrong"})
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.email, "")
        self.assertRedirects(self.client.post(reverse("account_email"), {"email": "owner@example.com", "current_password": PASSWORD}), reverse("dashboard"))
        user.refresh_from_db()
        self.assertEqual(user.email, "owner@example.com")

    def test_password_reset_link_changes_password_and_cannot_be_reused(self):
        user = User.objects.create_user("owner", email="owner@example.com", password=PASSWORD)
        self.assertRedirects(self.client.post(reverse("password_reset"), {"email": user.email}), reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        reset_url = re.search(r"https?://[^\s]+/reset/[^\s]+", mail.outbox[0].body).group(0)
        response = self.client.get(reset_url, follow=True)
        self.assertTrue(response.context["validlink"])
        form_url = response.request["PATH_INFO"]
        new_password = "Updated-River-Password-392!"
        self.assertRedirects(self.client.post(form_url, {"new_password1": new_password, "new_password2": new_password}), reverse("password_reset_complete"))
        user.refresh_from_db()
        self.assertTrue(user.check_password(new_password))
        self.assertFalse(self.client.get(reset_url, follow=True).context["validlink"])
        self.assertTrue(self.client.login(username=user.username, password=new_password))

    def test_unknown_email_gets_same_reset_confirmation(self):
        self.assertRedirects(self.client.post(reverse("password_reset"), {"email": "unknown@example.com"}), reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)


class ExpiryItemTests(CleanScanTestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("owner", password=PASSWORD)
        self.other = User.objects.create_user("other", password=PASSWORD)
        self.client.force_login(self.owner)
        self.data = {"name": "Business permit", "expiry_date": "2027-03-12", "notes": "Renew annually"}

    def test_create_edit_and_delete_without_attachment(self):
        self.assertRedirects(self.client.post(reverse("add_item"), self.data), reverse("dashboard"))
        item = ExpiryItem.objects.get()
        self.assertEqual(item.user, self.owner)
        self.assertFalse(item.document)
        self.assertRedirects(self.client.post(reverse("edit_item", args=[item.pk]), {**self.data, "name": "Updated permit"}), reverse("dashboard"))
        item.refresh_from_db()
        self.assertEqual(item.name, "Updated permit")
        confirmation = reverse("delete_item", args=[item.pk])
        self.assertEqual(self.client.get(confirmation).status_code, 200)
        self.assertTrue(ExpiryItem.objects.filter(pk=item.pk).exists())
        self.assertRedirects(self.client.post(confirmation), reverse("dashboard"))
        self.assertFalse(ExpiryItem.objects.exists())

    def test_invalid_date_shows_validation_error(self):
        response = self.client.post(reverse("add_item"), {**self.data, "expiry_date": "not-a-date"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("expiry_date", response.context["form"].errors)
        self.assertFalse(ExpiryItem.objects.exists())

    def test_another_user_cannot_view_edit_or_delete_item(self):
        item = ExpiryItem.objects.create(user=self.other, name="Private other-user document", expiry_date="2027-03-12")
        self.assertNotContains(self.client.get(reverse("dashboard")), item.name)
        for route in ("edit_item", "delete_item"):
            url = reverse(route, args=[item.pk])
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, self.data).status_code, 404)
        item.refresh_from_db()
        self.assertEqual(item.user, self.other)
        self.assertEqual(item.name, "Private other-user document")

    def test_dashboard_date_boundaries(self):
        today = date(2027, 1, 10)
        items = {days: ExpiryItem.objects.create(user=self.owner, name=f"Due {days}", expiry_date=today + timedelta(days=days)) for days in [-1, 0, 30, 31, 90, 91]}
        with patch("expiry.models.timezone.localdate", return_value=today):
            response = self.client.get(reverse("dashboard"))
        for key, days in [("expired_items", [-1]), ("within_30_days", [0, 30]), ("within_90_days", [31, 90]), ("later_items", [91])]:
            self.assertEqual([item.pk for item in response.context[key]], [items[day].pk for day in days])
        self.assertContains(response, "Expires today")

    def test_upload_keep_replace_and_clear_document(self):
        upload = SimpleUploadedFile("permit.pdf", b"%PDF-1.4\nexample", content_type="application/pdf")
        self.assertRedirects(self.client.post(reverse("add_item"), {**self.data, "document": upload}), reverse("dashboard"))
        item = ExpiryItem.objects.get()
        original = item.document.name
        url = reverse("edit_item", args=[item.pk])
        self.assertRedirects(self.client.post(url, {**self.data, "notes": "Changed without reupload"}), reverse("dashboard"))
        item.refresh_from_db()
        self.assertEqual(item.document.name, original)
        replacement = SimpleUploadedFile("renewed.pdf", b"%PDF-1.4\nrenewed", content_type="application/pdf")
        self.assertRedirects(self.client.post(url, {**self.data, "document": replacement}), reverse("dashboard"))
        item.refresh_from_db()
        self.assertIn("renewed", item.document.name)
        self.assertRedirects(self.client.post(url, {**self.data, "document-clear": "on"}), reverse("dashboard"))
        item.refresh_from_db()
        self.assertFalse(item.document)

    def test_upload_rejects_invalid_extension_or_mismatched_mime(self):
        for name, mime in [("payload.exe", "application/octet-stream"), ("permit.pdf", "image/png")]:
            with self.subTest(name=name):
                file = SimpleUploadedFile(name, b"test", content_type=mime)
                response = self.client.post(reverse("add_item"), {**self.data, "document": file})
                self.assertEqual(response.status_code, 200)
                self.assertIn("document", response.context["form"].errors)
        self.assertFalse(ExpiryItem.objects.exists())

    def test_upload_limit_allows_10mb_and_rejects_larger(self):
        for size, expected in [(10 * 1024 * 1024, True), (10 * 1024 * 1024 + 1, False)]:
            with self.subTest(size=size):
                upload = SimpleUploadedFile("large.pdf", b"x" * size, content_type="application/pdf")
                form = ExpiryItemForm(self.data, {"document": upload})
                self.assertEqual(form.is_valid(), expected)

    @override_settings(DOCUMENT_UPLOADS_ENABLED=False)
    def test_unconfigured_production_uploads_show_error_without_losing_item_data(self):
        upload = SimpleUploadedFile("permit.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post(reverse("add_item"), {**self.data, "document": upload})
        self.assertEqual(response.status_code, 200)
        self.assertIn("document", response.context["form"].errors)
        self.assertContains(response, "Business permit")
        self.assertRedirects(self.client.post(reverse("add_item"), self.data), reverse("dashboard"))


class ReminderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", email="owner@example.com", password=PASSWORD)
        self.client.force_login(self.user)
        self.today = date(2027, 6, 1)
        self.due = ExpiryItem.objects.create(user=self.user, name="Due permit", expiry_date=self.today + timedelta(days=7))

    def test_reminder_cannot_be_sent_by_get(self):
        self.assertEqual(self.client.get(reverse("test_email")).status_code, 405)
        self.assertEqual(len(mail.outbox), 0)

    def test_summary_uses_current_users_items_and_saved_email(self):
        other = User.objects.create_user("other", email="other@example.com")
        ExpiryItem.objects.create(user=other, name="Private contract", expiry_date=self.today)
        with patch("expiry.views.timezone.localdate", return_value=self.today):
            self.assertRedirects(self.client.post(reverse("test_email")), reverse("dashboard"))
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn(self.due.name, mail.outbox[0].body)
        self.assertNotIn("Private contract", mail.outbox[0].body)
        self.assertNotEqual(mail.outbox[0].from_email, "test@example.com")

    def test_missing_email_directs_user_to_settings_without_sending(self):
        self.user.email = ""
        self.user.save()
        self.assertRedirects(self.client.post(reverse("test_email")), reverse("account_email"))
        self.assertEqual(len(mail.outbox), 0)

    def test_smtp_failure_keeps_dashboard_usable(self):
        with patch("expiry.views.timezone.localdate", return_value=self.today), patch("expiry.views.send_mail", side_effect=SMTPException("SMTP unavailable")), self.assertLogs("expiry.views", level="ERROR"):
            response = self.client.post(reverse("test_email"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The reminder could not be sent")

    def test_daily_command_uses_local_date_and_exact_reminder_days(self):
        ExpiryItem.objects.create(user=self.user, name="Not due for reminder", expiry_date=self.today + timedelta(days=8))
        with patch("expiry.reminders.timezone.localdate", return_value=self.today):
            call_command("send_expiry_reminders", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.due.name, mail.outbox[0].body)
        self.assertNotIn("Not due for reminder", mail.outbox[0].body)
