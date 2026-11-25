from django.core.management.base import BaseCommand

from expiry.import_cleanup import cleanup_import_batches


class Command(BaseCommand):
    help = "Remove expired import draft metadata without deleting confirmed expiry items."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        count = cleanup_import_batches(options["limit"])
        self.stdout.write(f"Expired import batches removed: {count}")
