from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from expiry.deletions import cleanup_documents
from expiry.models import ExpiryItem, ReminderDelivery
from expiry.reminders import REMINDER_DAYS, email_is_configured, send_reminders


class Command(BaseCommand):
    help = "Send 30/14/7/1-day reminders once per item and expiry date; retry failed deliveries."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report counts without sending mail or changing records.")
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        if options["dry_run"]:
            today = timezone.localdate()
            dates = [today+timedelta(days=days) for days in REMINDER_DAYS]
            due = ExpiryItem.objects.filter(expiry_date__in=dates, user__isnull=False).exclude(user__email="").count()
            pending = ReminderDelivery.objects.filter(sent_at__isnull=True, expiry_date__gte=today).count()
            self.stdout.write(f"Dry run: {due} items at reminder dates; {pending} pending deliveries; email configured: {email_is_configured()}")
            return
        if not email_is_configured():
            raise CommandError("Configure the SMTP service and authorized sender before enabling scheduled email.")
        cleanup_documents(limit=50)
        result = send_reminders(limit=max(1, min(options["limit"], 1000)))
        self.stdout.write(f"Sent: {result['sent']}; failed: {result['failed']}; pending: {result['pending']}")
        if result["failed"]:
            raise CommandError("Some email deliveries failed; they remain queued for retry.")
