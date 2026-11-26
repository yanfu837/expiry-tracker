import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes


@override_settings(SUPPORT_EMAIL="support@example.com")
class PasswordRecoveryTests(TestCase):
    def test_request_sends_immediately_to_registered_email_with_https_and_expiry(self):
        user = User.objects.create_user("self-service", email="customer@example.com", password="Recovery-Test-631!")
        with patch("expiry.scheduled_reminders.send_reminders") as scheduled:
            response = self.client.post(reverse("password_reset"), {"email": user.email}, secure=True)
        self.assertRedirects(response, reverse("password_reset_done"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn("https://testserver/reset/", mail.outbox[0].body)
        self.assertIn("60 minutes", mail.outbox[0].body)
        self.assertIn("single-use", mail.outbox[0].body)
        self.assertIn("support@example.com", mail.outbox[0].body)
        scheduled.assert_not_called()

    def test_expired_link_cannot_change_password(self):
        user = User.objects.create_user("expired-recovery", email="expired@example.com", password="Recovery-Test-632!")
        original = user.password
        past = default_token_generator._now()-timedelta(minutes=61)
        with patch.object(default_token_generator, "_now", return_value=past):
            token = default_token_generator.make_token(user)
        url = reverse("password_reset_confirm", args=[urlsafe_base64_encode(force_bytes(user.pk)), token])
        response = self.client.get(url, follow=True)
        self.assertFalse(response.context["validlink"])
        user.refresh_from_db()
        self.assertEqual(user.password, original)

    def test_help_links_use_configured_mailbox_on_recovery_and_privacy_pages(self):
        for route in ("password_reset", "password_reset_done", "privacy", "login"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertContains(response, 'href="mailto:support@example.com"')
        with override_settings(EMAIL_TEST_MODE=False):
            response = self.client.get(reverse("password_reset"))
            self.assertEqual(response.status_code, 503)
            self.assertContains(response, 'href="mailto:support@example.com"', status_code=503)

    def test_account_without_email_still_gets_private_help_not_an_unsafe_reset(self):
        user = User.objects.create_user("legacy-no-email", password="Recovery-Test-633!")
        original = user.password
        response = self.client.post(reverse("password_reset"), {"email": "unknown@example.com"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)
        help_page = self.client.get(reverse("password_reset_done"))
        self.assertContains(help_page, "cannot access that inbox")
        user.refresh_from_db()
        self.assertEqual(user.password, original)

    @override_settings(SUPPORT_EMAIL='"help?subject=changed"@example.com')
    def test_mailbox_characters_cannot_inject_mailto_parameters(self):
        response = self.client.get(reverse("password_reset"))
        self.assertContains(response, 'mailto:%22help%3Fsubject%3Dchanged%22@example.com')
        self.assertNotContains(response, 'href="mailto:"help?subject=changed')

    @override_settings(SUPPORT_EMAIL="")
    def test_missing_support_setting_does_not_render_an_empty_mailto_link(self):
        response = self.client.get(reverse("password_reset"))
        self.assertNotContains(response, 'href="mailto:"')
