from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import ExpiryItem


class ContractorIntroductionTests(TestCase):
    def test_public_page_exposes_only_examples_and_keeps_account_routes_protected(self):
        owner = User.objects.create_user("private-contractor", password="Contractor-Test-627!")
        ExpiryItem.objects.create(user=owner, name="Customer confidential policy", notes="Private policy reference", expiry_date=timezone.localdate())
        response = self.client.get(reverse("contractors"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Customer confidential policy")
        self.assertNotContains(response, "Private policy reference")
        self.assertNotContains(response, owner.username)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)
        self.assertEqual(self.client.get(reverse("add_item")).status_code, 302)
        self.assertEqual(self.client.post(reverse("contractors")).status_code, 405)

    @override_settings(EMAIL_TEST_MODE=False, DOCUMENT_UPLOADS_ENABLED=False)
    def test_page_reports_unavailable_email_and_uploads_without_promising_them(self):
        response = self.client.get(reverse("contractors"))
        self.assertContains(response, "Email reminders are not enabled on this site yet")
        self.assertContains(response, "Document uploads are currently unavailable")
        self.assertNotContains(response, "Attach a supported document to its expiry item")

    def test_logged_in_visitor_keeps_the_same_account_without_public_response_caching(self):
        owner = User.objects.create_user("returning-contractor", password="Contractor-Test-628!")
        self.client.force_login(owner)
        response = self.client.get(reverse("contractors"))
        self.assertContains(response, 'href="'+reverse("dashboard")+'"')
        self.assertContains(response, "Open your expiry list")
        self.assertNotContains(response, "Create your account")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(User.objects.count(), 1)
