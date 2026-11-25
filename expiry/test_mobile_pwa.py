from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ExpiryItem


class MobilePwaTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("mobile-owner", password="Mobile-Private-732!")
        self.other = User.objects.create_user("mobile-other", password="Mobile-Private-733!")
        self.today = timezone.localdate()
        for days in (-1, 0, 30, 31, 90, 91):
            ExpiryItem.objects.create(user=self.owner, name=f"Policy {days}", expiry_date=self.today+timedelta(days=days), notes="Renewal reference")
        ExpiryItem.objects.create(user=self.other, name="Private other customer", expiry_date=self.today, notes="Renewal reference")

    def test_manifest_is_public_installable_and_has_real_icons(self):
        response = self.client.get(reverse("pwa_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        data = response.json()
        self.assertEqual(data["lang"], "en")
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(data["scope"], "/")
        self.assertEqual(data["display"], "standalone")
        self.assertEqual({icon["sizes"] for icon in data["icons"]}, {"192x192", "512x512"})
        self.assertTrue(any(icon["purpose"] == "maskable" for icon in data["icons"]))
        for icon in data["icons"]:
            self.assertTrue(finders.find(icon["src"].removeprefix("/static/")))
        self.assertNotIn("Cookie", response.get("Vary", ""))
        self.assertFalse(response.cookies)

    def test_worker_is_public_root_scoped_and_revalidated(self):
        response = self.client.get(reverse("pwa_service_worker"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertIn("no-cache", response["Cache-Control"])
        self.assertTrue(response["Content-Type"].startswith("text/javascript"))
        self.assertFalse(response.cookies)
        self.assertEqual(self.client.post(reverse("pwa_service_worker")).status_code, 405)

    def test_offline_screen_does_not_expose_logged_in_customer_data(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("pwa_offline"))
        self.assertContains(response, "You are offline")
        self.assertNotContains(response, self.owner.username)
        self.assertNotContains(response, "Policy 30")
        self.assertNotContains(response, "csrfmiddlewaretoken")
        self.assertNotIn("Cookie", response.get("Vary", ""))
        self.assertFalse(response.cookies)

    def test_search_is_limited_to_current_account_and_matches_notes(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard"), {"q": "Renewal reference"})
        self.assertEqual(response.context["total_items"], 6)
        self.assertNotContains(response, "Private other customer")
        response = self.client.get(reverse("dashboard"), {"q": "Policy 30"})
        self.assertEqual(response.context["total_items"], 1)
        response = self.client.get(reverse("dashboard"), {"q": "Private other"})
        self.assertEqual(response.context["total_items"], 0)

    def test_date_filters_have_correct_boundaries(self):
        self.client.force_login(self.owner)
        for window, expected in [("expired", [-1]), ("30", [0, 30]), ("90", [31, 90]), ("later", [91])]:
            with self.subTest(window=window):
                response = self.client.get(reverse("dashboard"), {"window": window})
                names = [item.name for section in response.context["sections"] for item in section["items"]]
                self.assertEqual(names, [f"Policy {day}" for day in expected])
        response = self.client.get(reverse("dashboard"), {"window": "invalid"})
        self.assertEqual(response.context["window"], "all")
        self.assertEqual(response.context["total_items"], 6)

    def test_private_pages_remain_login_protected_and_not_cacheable(self):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)
        self.client.force_login(self.owner)
        for name in ("dashboard", "add_item", "account_email"):
            response = self.client.get(reverse(name))
            self.assertIn("no-store", response["Cache-Control"])
            self.assertContains(response, 'name="viewport"')
            self.assertContains(response, 'rel="manifest"')
        self.assertContains(self.client.get(reverse("dashboard")), 'aria-label="Main navigation"')
