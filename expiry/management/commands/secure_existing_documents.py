from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from expiry.models import ExpiryItem
from expiry.storage import DocumentStorageError, PRIVATE_PREFIX


class Command(BaseCommand):
    help = "Move referenced legacy public documents to authenticated storage."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count legacy documents without changing files or rows.")

    def handle(self, *args, **options):
        if not hasattr(default_storage, "secure_existing"):
            self.stdout.write("Local documents are served only through the protected download view.")
            return
        moved = 0
        failed = 0
        items = ExpiryItem.objects.exclude(document="").exclude(document__isnull=True).exclude(document__startswith=PRIVATE_PREFIX)
        if options["dry_run"]:
            try:
                public_assets = sum(1 for _ in default_storage.legacy_public_names())
            except DocumentStorageError:
                raise CommandError("Legacy document inventory is unavailable.") from None
            self.stdout.write(f"Dry run: {items.count()} legacy references; {public_assets} app-tagged public assets.")
            return
        for item in items.iterator():
            previous = item.document.name
            try:
                name = default_storage.secure_existing(previous)
            except DocumentStorageError:
                failed += 1
                self.stderr.write(f"Could not secure document for item {item.pk}; retry after checking storage access.")
                continue
            moved += ExpiryItem.objects.filter(pk=item.pk, document=previous).update(document=name)
        # Restrict inventory to the legacy app folder AND its original storage tag.
        # Keep unreferenced old assets private; this command does not delete them.
        archived = 0
        try:
            legacy_names = list(default_storage.legacy_public_names())
        except DocumentStorageError:
            raise CommandError("Legacy document inventory failed; retry after checking storage access.") from None
        for previous in legacy_names:
            try:
                name = default_storage.secure_existing(previous)
            except DocumentStorageError:
                failed += 1
                continue
            references = ExpiryItem.objects.filter(document=previous).update(document=name)
            moved += references
            archived += not references
        self.stdout.write(f"Secured references: {moved}; private archived assets: {archived}; failed: {failed}")
        if failed:
            raise CommandError("Some legacy documents could not be secured; no public download fallback is enabled.")
