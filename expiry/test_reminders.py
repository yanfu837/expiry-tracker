from datetime import timedelta
from io import StringIO
from smtplib import SMTPException
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import ExpiryItem, ReminderDelivery
from .reminders import send_reminders


class ReminderDeliveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("reminder_owner", email="owner@example.com", password="ReminderPass-342!")
        self.today = timezone.localdate()

    def item(self, days, user=None, name=None):
        return ExpiryItem.objects.create(user=user or self.user, name=name or f"Policy {days}", expiry_date=self.today+timedelta(days=days))

    def test_exact_four_thresholds_and_account_isolation(self):
        for days in [30,14,7,1,0,2,8,15,31]:
            self.item(days, name=f"DAYS_{days}_END")
        other = User.objects.create_user("reminder_other", email="other@example.com")
        self.item(7, user=other, name="Other account private policy")
        result = send_reminders()
        self.assertEqual(result["sent"], 5)
        self.assertEqual(len(mail.outbox), 2)
        owner = next(m for m in mail.outbox if m.to == [self.user.email])
        for days in [30,14,7,1]:
            self.assertIn(f"DAYS_{days}_END", owner.body)
        for days in [0,2,8,15,31]:
            self.assertNotIn(f"DAYS_{days}_END", owner.body)
        self.assertNotIn("Other account private policy", owner.body)

    def test_same_day_rerun_does_not_send_again(self):
        self.item(30)
        self.assertEqual(send_reminders()["sent"], 1)
        self.assertEqual(send_reminders()["sent"], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(ReminderDelivery.objects.count(), 1)
        self.assertIsNotNone(ReminderDelivery.objects.get().sent_at)

    def test_failed_send_is_retried_without_repeating_successes(self):
        self.item(7)
        with patch("expiry.reminders.EmailMessage.send", side_effect=SMTPException("sensitive provider error")):
            self.assertEqual(send_reminders()["failed"], 1)
        delivery = ReminderDelivery.objects.get()
        self.assertIsNone(delivery.sent_at)
        self.assertEqual(delivery.last_error, "SMTPException")
        self.assertEqual(send_reminders()["sent"], 0)
        self.assertEqual(len(mail.outbox), 0)
        delivery.retry_at = timezone.now()-timedelta(seconds=1)
        delivery.save(update_fields=["retry_at"])
        self.assertEqual(send_reminders()["sent"], 1)
        self.assertEqual(send_reminders()["sent"], 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_changing_expiry_date_allows_a_new_reminder(self):
        item = self.item(30)
        send_reminders()
        item.expiry_date = self.today+timedelta(days=7)
        item.save(update_fields=["expiry_date"])
        self.assertEqual(send_reminders()["sent"], 1)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(ReminderDelivery.objects.count(), 2)

    def test_live_lease_blocks_another_run_but_expired_lease_can_be_recovered(self):
        item = self.item(1)
        delivery = ReminderDelivery.objects.create(item=item, expiry_date=item.expiry_date, days_before=1, lease_token=uuid4(), lease_until=timezone.now()+timedelta(minutes=5))
        self.assertEqual(send_reminders()["sent"], 0)
        delivery.lease_until = timezone.now()-timedelta(seconds=1)
        delivery.save(update_fields=["lease_until"])
        self.assertEqual(send_reminders()["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_failure_for_one_account_does_not_block_other_accounts(self):
        self.item(7)
        other = User.objects.create_user("second_owner", email="second@example.com")
        self.item(7, user=other)
        original = mail.EmailMessage.send
        def send(message, **kwargs):
            if message.to == [self.user.email]:
                raise SMTPException("unavailable")
            return original(message, **kwargs)
        with patch("expiry.reminders.EmailMessage.send", autospec=True, side_effect=send):
            result = send_reminders()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(mail.outbox[0].to, [other.email])

    def test_pending_delivery_uses_updated_email_address(self):
        item = self.item(7)
        ReminderDelivery.objects.create(item=item, expiry_date=item.expiry_date, days_before=7)
        self.user.email = "updated@example.com"
        self.user.save(update_fields=["email"])
        send_reminders()
        self.assertEqual(mail.outbox[0].to, ["updated@example.com"])

    def test_dry_run_does_not_send_or_write_delivery_records(self):
        self.item(14)
        output = StringIO()
        call_command("send_expiry_reminders", dry_run=True, stdout=output)
        self.assertIn("Dry run", output.getvalue())
        self.assertFalse(ReminderDelivery.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(MAILERS={"default":{"BACKEND":"django.core.mail.backends.smtp.EmailBackend", "OPTIONS":{}}})
    def test_missing_email_configuration_blocks_send_and_reports_unavailable(self):
        self.item(14)
        with self.assertRaises(CommandError):
            call_command("send_expiry_reminders", stdout=StringIO())
        self.assertFalse(ReminderDelivery.objects.exists())
        response = self.client.post(reverse("password_reset"), {"email":"owner@example.com"})
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "not available yet", status_code=503)
        self.assertEqual(len(mail.outbox), 0)
