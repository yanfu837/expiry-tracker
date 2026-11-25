# Batch import and confirmation

This extends the pending private scanning/OCR work in PR #4. Hosting and costs remain undecided. It has not been deployed and does not send documents to external AI APIs.

## Workflow and limits

1. Select up to **10 source files**, **10 MB each**, **50 MB combined**. The browser checks limits before any request; the server also checks the manifest and actual upload size. Oversized selections offer choosing other files or creating an item without a document.
2. Files are scanned and extracted sequentially on the private service. The screen shows real upload progress, followed by scanning/extraction status. PDF extraction still covers only the **first 10 pages**, clearly stated before selection.
3. Review filename, item name, expiry date, original date text and notes together. Missing or conflicting expiry dates stay blank. Failed files can be retried or skipped individually.
4. Explicitly confirm each valid item. Any edit or changed suggestion clears its confirmation. The interface is English; customer content and original source excerpts are displayed as entered.
5. Save confirmed items with one action. Each row commits independently; successful rows remain saved when another fails. Phone users see the same review rows as stacked cards; desktop users see a wider table.

## An expiry item is the core unit

A candidate represents one matter requiring a reminder. Its identity is a UUID, independent of filename or file hash. A source file can supply several matters using **Add another item**; the additional row starts with a blank date and requires separate review. The default limit is 50 candidate matters per batch. Saved items have independent private attachment copies, so deleting one does not delete another's attachment.

The 50 MB limit measures the selected source files. Analysis, saving and retries transfer files separately, so it is not a total network-traffic limit. Each request carries one file and retains the existing 10 MB streaming limit.

## Confirmation, retries and reminders

- `DocumentImportBatch` and `DocumentImportCandidate` keep draft suggestions separate from `ExpiryItem`. Analysis does not store a permanent attachment or create a reminder delivery.
- Only an explicit confirmation with valid fields, a clean scan and matching file hash creates an expiry item. Reminder jobs still read only `ExpiryItem`.
- Per-row database leases prevent concurrent duplicate saves. Item creation and the saved receipt commit in one transaction.
- If a successful save response is lost, retries return the saved result. A saved tombstone also prevents recreation after the item is deleted.
- Storage or database failures leave the row retryable and use the existing durable attachment cleanup mechanism for uncommitted uploads.
- Saved rows are locked; other rows can continue independently. Expired leases can recover after an interrupted request.

## Privacy and retention

All draft APIs require login, ownership and CSRF validation, with no-store responses. Scan tokens remain server-side. Original files remain in the current browser page's File objects and private request/processing temporary storage; they are not stored in localStorage or Cloudinary until confirmed. Confirmed attachments retain authenticated private Cloudinary storage.

Unsaved manual edits, checkboxes and file selections are page-local. Refreshing requires reviewing and reselecting original files. Draft metadata expires after 24 hours. Cleanup runs at startup, on new batches and during authenticated non-preview daily reminder requests; dry runs never mutate it. `python manage.py cleanup_import_batches --limit 100` is also available. Cleanup leaves saved items and their attachments intact.

## Deployment and checks

Migration `0005_documentimportbatch_documentimportcandidate` adds only draft tables. It does not change existing items or introduce audience restrictions. The scanning service must be ready before deploying PR #4; when unavailable, new attachments are blocked while manual items without attachments remain usable. Mobile/PWA support can be deployed independently through its separate pull request.

The test suite covers limits, confirmation, account isolation, original evidence, reminder exclusion, individual retries, partial failures, leases, changed files, stale scan receipts, same-file multiple matters, cleanup and lost-response idempotency. Browser checks use isolated fixtures and a simulated processor for the review workflow; production scanner and real-phone installation must be validated after the relevant services are deployed.
