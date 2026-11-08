"""Persist reminder deliveries so normal retries and concurrent runs are safe."""
import hashlib
import logging
from collections import defaultdict
from datetime import timedelta
from smtplib import SMTPException
from uuid import uuid4
from email.utils import parseaddr
from itertools import islice

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import F, Q
from django.utils import timezone

from .models import ExpiryItem, ReminderDelivery

REMINDER_DAYS = (30, 14, 7, 1)
logger = logging.getLogger(__name__)


def email_is_configured():
    mailer = settings.MAILERS["default"]
    backend = mailer["BACKEND"]
    if backend == "django.core.mail.backends.locmem.EmailBackend":
        return getattr(settings, "EMAIL_TEST_MODE", False)
    if backend != "django.core.mail.backends.smtp.EmailBackend":
        return False
    options = mailer.get("OPTIONS", {})
    return bool(options.get("host") and options.get("username") and options.get("password") and
                settings.DEFAULT_FROM_EMAIL and not settings.DEFAULT_FROM_EMAIL.endswith("@localhost"))


def enqueue_reminders(today):
    for days in REMINDER_DAYS:
        items = ExpiryItem.objects.filter(expiry_date=today+timedelta(days=days), user__isnull=False).exclude(user__email="")
        for item in items.iterator():
            ReminderDelivery.objects.get_or_create(item=item, expiry_date=item.expiry_date, days_before=days)


def reminder_preview():
    today = timezone.localdate()
    dates = [today+timedelta(days=days) for days in REMINDER_DAYS]
    return {
        "date": today.isoformat(),
        "due": ExpiryItem.objects.filter(expiry_date__in=dates, user__isnull=False).exclude(user__email="").count(),
        "pending": ReminderDelivery.objects.filter(sent_at__isnull=True, expiry_date__gte=today).count(),
        "email_configured": email_is_configured(),
    }


def ready_deliveries(today, now):
    return ReminderDelivery.objects.filter(
        sent_at__isnull=True, retry_at__lte=now, expiry_date__gte=today, item__user__isnull=False,
    ).exclude(item__user__email="").filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))


def send_reminders(limit=200, max_accounts=None):
    today = timezone.localdate()
    enqueue_reminders(today)
    now = timezone.now()
    # Do not send stale reminders after an item's expiry date was edited.
    ReminderDelivery.objects.filter(sent_at__isnull=True).exclude(expiry_date=F("item__expiry_date")).delete()
    candidates = ready_deliveries(today, now)
    groups = defaultdict(list)
    for pk, user_id in candidates.order_by("pk").values_list("pk", "item__user_id")[:limit]:
        groups[user_id].append(pk)
    sent = 0
    failed = 0
    # HTTP-triggered runs send one account's digest at a time so a slow SMTP
    # connection cannot hold a web worker across many accounts.
    account_groups = groups.items() if max_accounts is None else islice(groups.items(), max_accounts)
    for user_id, ids in account_groups:
        token = uuid4()
        claimed_at = timezone.now()
        ReminderDelivery.objects.filter(
            pk__in=ids, sent_at__isnull=True, retry_at__lte=claimed_at,
            item__user_id=user_id, expiry_date=F("item__expiry_date"),
        ).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=claimed_at)).update(
            lease_token=token, lease_until=claimed_at+timedelta(minutes=10), attempts=F("attempts")+1,
        )
        deliveries = list(ReminderDelivery.objects.filter(
            lease_token=token, sent_at__isnull=True,
        ).select_related("item__user").order_by("pk"))
        if not deliveries:
            continue
        recipient = deliveries[0].item.user.email
        lines = ["You have upcoming expiry reminders:", ""]
        for delivery in deliveries:
            item = delivery.item
            remaining = (item.expiry_date-today).days
            lines.extend([f"- {item.name} — {remaining} days remaining", f"  Expiry date: {item.expiry_date}", ""])
        digest = hashlib.sha256(",".join(
            f"{d.pk}:{d.item_id}:{d.expiry_date}:{d.days_before}:{d.item.created_at.isoformat()}"
            for d in deliveries
        ).encode()).hexdigest()[:32]
        sender_domain = parseaddr(settings.DEFAULT_FROM_EMAIL)[1].rpartition("@")[2] or "localhost"
        message = EmailMessage(
            f"Expiry Tracker: {len(deliveries)} expiry reminder(s)", "\n".join(lines),
            settings.DEFAULT_FROM_EMAIL, [recipient],
            headers={"Message-ID": f"<expiry-{digest}@{sender_domain}>"},
        )
        records = ReminderDelivery.objects.filter(pk__in=[d.pk for d in deliveries], lease_token=token, sent_at__isnull=True)
        try:
            if message.send(fail_silently=False) != 1:
                raise OSError("Email was not accepted for delivery.")
        except (SMTPException, OSError, ValueError) as error:
            retry_minutes = min(24*60, 15*2**min(max(d.attempts for d in deliveries)-1, 7))
            records.update(lease_until=None, lease_token=None, retry_at=now+timedelta(minutes=retry_minutes), last_error=type(error).__name__[:100])
            failed += len(deliveries)
            logger.warning("Reminder delivery for account %s will be retried (%s)", deliveries[0].item.user_id, type(error).__name__)
        else:
            sent += records.update(sent_at=timezone.now(), lease_until=None, lease_token=None, last_error="")
    return {
        "sent": sent, "failed": failed, "ready": ready_deliveries(today, timezone.now()).count(),
        "pending": ReminderDelivery.objects.filter(sent_at__isnull=True, expiry_date__gte=today).count(),
    }
