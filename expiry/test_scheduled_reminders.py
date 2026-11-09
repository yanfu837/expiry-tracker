import json
import time
from datetime import timedelta
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import ExpiryItem, ReminderDelivery


AUDIENCE = "https://expiry-tracker.example/internal/reminders/run/"
WORKFLOW = "yanfu837/expiry-tracker/.github/workflows/expiry-reminders.yml@refs/heads/main"


@override_settings(
    REMINDER_OIDC_AUDIENCE=AUDIENCE,
    REMINDER_OIDC_REPOSITORY_ID="1372462294",
    REMINDER_OIDC_OWNER_ID="93774500",
    REMINDER_OIDC_WORKFLOW_REF=WORKFLOW,
)
class ScheduledReminderTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.signer.public_key()))
        public_jwk["kid"] = "test-github-key"
        cls.signing_key = jwt.PyJWK.from_dict(public_jwk)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.url = reverse("scheduled_reminders")
        patcher = patch("expiry.scheduled_reminders.github_keys.get_signing_keys", return_value=[self.signing_key])
        self.keys = patcher.start()
        self.addCleanup(patcher.stop)
        self.owner = User.objects.create_user("schedule_owner", email="owner@example.com", password="Schedule-Owner-937!")
        self.other = User.objects.create_user("schedule_other", email="other@example.com")

    def token(self, **changes):
        now = int(time.time())
        claims = {
            "iss": "https://token.actions.githubusercontent.com", "aud": AUDIENCE,
            "iat": now, "nbf": now-1, "exp": now+300,
            "sub": "repo:yanfu837@93774500/expiry-tracker@1372462294:ref:refs/heads/main",
            "repository_id": "1372462294", "repository_owner_id": "93774500",
            "repository": "yanfu837/expiry-tracker", "ref": "refs/heads/main",
            "workflow_ref": WORKFLOW, "event_name": "schedule",
        }
        claims.update(changes)
        return jwt.encode(claims, self.signer, algorithm="RS256", headers={"kid": "test-github-key"})

    def post(self, body=None, token=None):
        return self.client.post(self.url, data=json.dumps(body or {}), content_type="application/json",
                                HTTP_AUTHORIZATION="Bearer " + (token or self.token()))

    def item(self, user, days):
        return ExpiryItem.objects.create(user=user, name=f"Private policy {user.pk}/{days}",
                                         expiry_date=timezone.localdate()+timedelta(days=days))

    def test_anonymous_or_logged_in_users_cannot_trigger_without_a_token(self):
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.keys.assert_not_called()
        self.assertFalse(ReminderDelivery.objects.exists())

    def test_only_post_is_allowed_and_responses_are_not_cached(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertIn("no-store", response["Cache-Control"])
        self.keys.assert_not_called()

    @override_settings(REMINDER_OIDC_REPOSITORY_ID="")
    def test_missing_trust_configuration_fails_closed(self):
        self.assertEqual(self.post().status_code, 503)
        self.keys.assert_not_called()

    def test_signed_tokens_from_other_contexts_are_rejected(self):
        invalid_claims = [
            {"repository_id": "999"}, {"repository_owner_id": "999"},
            {"repository": "attacker/expiry-tracker"},
            {"workflow_ref": WORKFLOW.replace("expiry-reminders.yml", "other.yml")},
            {"ref": "refs/heads/untrusted"}, {"event_name": "pull_request"},
            {"event_name": "push"}, {"sub": "repo:yanfu837/expiry-tracker:pull_request"},
            {"aud": "another-service"}, {"iss": "https://attacker.example"},
            {"exp": int(time.time())-120}, {"nbf": int(time.time())+120},
        ]
        for changes in invalid_claims:
            with self.subTest(changes=changes):
                self.assertEqual(self.post(token=self.token(**changes)).status_code, 403)
        self.assertFalse(ReminderDelivery.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_required_claim_is_rejected(self):
        claims = jwt.decode(self.token(), options={"verify_signature": False})
        del claims["repository_id"]
        token = jwt.encode(claims, self.signer, algorithm="RS256", headers={"kid": "test-github-key"})
        self.assertEqual(self.post(token=token).status_code, 403)

    def test_forged_signature_is_rejected(self):
        attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = jwt.decode(self.token(), options={"verify_signature": False})
        forged = jwt.encode(claims, attacker, algorithm="RS256", headers={"kid": "test-github-key"})
        self.assertEqual(self.post(token=forged).status_code, 403)

    def test_wrong_algorithm_or_malformed_token_never_fetches_keys(self):
        token = jwt.encode({"sub": "attacker"}, "this-is-not-a-github-rsa-signature-111", algorithm="HS256",
                           headers={"kid": "test-github-key"})
        for invalid in [token, "invalid", "x"*9000]:
            self.assertEqual(self.post(token=invalid).status_code, 403)
        self.keys.assert_not_called()

    def test_identity_provider_outage_is_reported_without_details(self):
        self.keys.side_effect = jwt.PyJWKClientConnectionError("private network diagnostic")
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "identity_provider_unavailable"})

    def test_legacy_subject_is_safe_only_with_the_same_immutable_ids(self):
        response = self.post({"dry_run": True}, token=self.token(
            sub="repo:yanfu837/expiry-tracker:ref:refs/heads/main", event_name="workflow_dispatch",
        ))
        self.assertEqual(response.status_code, 200)

    def test_dry_run_does_not_write_or_send_or_expose_customer_details(self):
        self.item(self.owner, 30)
        response = self.post({"dry_run": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["due"], 1)
        self.assertNotIn(self.owner.email, response.content.decode())
        self.assertNotIn("Private policy", response.content.decode())
        self.assertFalse(ReminderDelivery.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_request_cannot_override_recipients_dates_or_trigger_options(self):
        for body in [{"recipient": "attacker@example.com"}, {"dry_run": "false"}, {"dry_run": 1}]:
            self.assertEqual(self.post(body).status_code, 400)
        response = self.client.post(self.url, data="x"*257, content_type="application/json",
                                    HTTP_AUTHORIZATION="Bearer " + self.token())
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ReminderDelivery.objects.exists())

    @override_settings(MAILERS={"default": {"BACKEND": "django.core.mail.backends.smtp.EmailBackend", "OPTIONS": {}}})
    def test_missing_smtp_blocks_real_run_but_allows_safe_preview(self):
        self.item(self.owner, 14)
        self.assertEqual(self.post().json(), {"error": "email_not_configured"})
        self.assertEqual(self.post().status_code, 503)
        response = self.post({"dry_run": True})
        self.assertFalse(response.json()["email_configured"])
        self.assertFalse(ReminderDelivery.objects.exists())

    def test_batched_runs_send_one_account_digest_and_reruns_skip_successes(self):
        for days in [30, 14, 7, 1]:
            self.item(self.owner, days)
        self.item(self.other, 7)
        first = self.post()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"sent": 4, "failed": 0, "pending": 1, "ready": 1})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.owner.email])
        self.assertNotIn(f"Private policy {self.other.pk}/", mail.outbox[0].body)
        self.assertEqual(self.post().json(), {"sent": 1, "failed": 0, "pending": 0, "ready": 0})
        self.assertEqual(self.post().json()["sent"], 0)
        self.assertEqual(len(mail.outbox), 2)

    def test_failed_batch_is_queued_and_does_not_block_the_next_account(self):
        self.item(self.owner, 7)
        self.item(self.other, 7)
        with patch("expiry.reminders.EmailMessage.send", side_effect=OSError("provider diagnostic")):
            result = self.post().json()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["ready"], 1)
        self.assertEqual(self.post().json()["sent"], 1)
        self.assertEqual(self.post().json()["sent"], 0)
        self.assertEqual(mail.outbox[0].to, [self.other.email])
