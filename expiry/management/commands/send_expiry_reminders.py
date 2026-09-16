from datetime import date

from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from expiry.models import ExpiryItem


class Command(BaseCommand):
    help = "Send expiry reminder emails at 30, 14, 7, and 1 days before expiry"

    def handle(self, *args, **options):

        today = date.today()

        # Only send reminders on these exact days
        reminder_days = [30, 14, 7, 1]

        # Get all users who have an email address
        users = User.objects.exclude(email="")

        for user in users:

            # Get this user's expiry items
            items = ExpiryItem.objects.filter(
                user=user
            )

            reminder_items = []

            # Check each item
            for item in items:

                days_remaining = (
                    item.expiry_date - today
                ).days

                # Only include items with exactly
                # 30, 14, 7, or 1 day remaining
                if days_remaining in reminder_days:
                    reminder_items.append(
                        (item, days_remaining)
                    )

            # If this user has nothing to remind today,
            # skip sending email
            if not reminder_items:
                continue

            # Build email message
            message = "You have upcoming expiry reminders:\n\n"

            for item, days_remaining in reminder_items:

                message += (
                    f"{item.name} — "
                    f"{days_remaining} days remaining\n"
                    f"Expiry date: {item.expiry_date}\n\n"
                )

            # Email subject
            subject = (
                f"Expiry Tracker: "
                f"{len(reminder_items)} expiry reminder(s)"
            )

            # Send email to this user
            send_mail(
                subject,
                message,
                None,
                [user.email],
                fail_silently=False,
            )

            # Show success message in Terminal
            self.stdout.write(
                self.style.SUCCESS(
                    f"Reminder sent to {user.email}"
                )
            )