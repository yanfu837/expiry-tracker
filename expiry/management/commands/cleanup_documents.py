from django.core.management.base import BaseCommand

from expiry.deletions import cleanup_documents


class Command(BaseCommand):
    help = "Retry committed document deletions without exposing file names."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        result = cleanup_documents(limit=max(1, min(options["limit"], 1000)))
        self.stdout.write(f"Deleted: {result['deleted']}; failed: {result['failed']}; pending: {result['pending']}")
