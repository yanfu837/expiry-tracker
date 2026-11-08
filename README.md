# Expiry Tracker

A Django 6.1 application for tracking licenses, insurance, contracts and document expiry dates.

## Local development

Use Python 3.12 or newer. Keep your existing `.env`, database and uploaded documents when applying updates.

```sh
python -m venv venv
# macOS / Linux:
source venv/bin/activate
# Windows PowerShell instead:
# .\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`. Generate a unique secret with:

```sh
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Put it in `SECRET_KEY` in your local `.env`, then run:

```sh
python manage.py migrate
python manage.py runserver
```

Visit <http://127.0.0.1:8000/>. Development uses local SQLite and local file uploads. Email features display an unavailable message until SMTP credentials are configured. Automated tests exercise email flows using an isolated in-memory mail backend.

## Update the existing Render service

See [DEPLOY.zh-TW.md](DEPLOY.zh-TW.md) for detailed instructions in Traditional Chinese.

Keep the existing web service and PostgreSQL database. Set these commands on the **existing** Render service:

- Build Command: `bash build.sh`
- Start Command: `bash start.sh`

The build installs dependencies and collects static assets. Startup applies existing Django migrations to the runtime database before starting Gunicorn. A failed migration aborts startup and prints the actual error to Render Logs.

The supplied production traceback identifies a separate configuration blocker:
`OperationalError: failed to resolve host ... Name or service not known`.
Restore the PostgreSQL connection first: verify the existing database is available,
check its region against the web service, and refresh `DATABASE_URL` from Render's
Connections page. An internal database hostname needs the supported same-region
private network. For cross-region access, use the provider's external connection
URL with appropriate network access and TLS settings. Migrations cannot repair
an unresolvable host, and the traceback alone does not establish that the database
has expired. See the diagnosis at the start of the Chinese deployment guide.

Required production environment values:

| Variable | Value |
| --- | --- |
| `SECRET_KEY` | Keep the current secret, or generate a long random secret for a new deployment. |
| `DEBUG` | `False` |
| `DATABASE_URL` | The Internal Database URL of the existing PostgreSQL database. |
| `ALLOWED_HOSTS` | The exact service hostname, plus any custom domains. Render's external hostname is also added automatically. |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated HTTPS origins for any custom domains. Render's external URL is added automatically. |
| `TIME_ZONE` | Your business timezone, e.g. `Asia/Taipei`; default `UTC`. |

Do not commit `.env` or database credentials. Do not replace or recreate an existing database to fix missing migrations. Render's local SQLite files are ephemeral, so the app refuses to start on Render without an explicit `DATABASE_URL`.

On Render with `DEBUG=False`, HTTPS redirects, secure session/CSRF cookies and HSTS are enabled. The trusted forwarded scheme is restricted to the Render environment. HSTS subdomain coverage and preload remain opt-in through `SECURE_HSTS_INCLUDE_SUBDOMAINS` and `SECURE_HSTS_PRELOAD`; enable them only after checking your domain setup.

### Email and password recovery

New accounts must provide an email. Existing users can add or change an email in **Email settings**, confirming their current password. The login page includes **Forgot your password?**. Accounts without an email need administrator help if the password is already lost.

Configure an SMTP service in Render to deliver real emails:

```text
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=<your SMTP account>
EMAIL_HOST_PASSWORD=<your SMTP app password>
DEFAULT_FROM_EMAIL=<a sender authorized by your SMTP provider>
```

Keep these values in Render's environment settings. Gmail requires an app password. Confirm that your Render service plan and email provider allow the configured SMTP connection; automated tests use an in-memory mail backend and do not establish production email deliverability.

Users can request an upcoming-items summary when email is configured. Automatic reminders use a durable delivery record per item, expiry date and threshold (30, 14, 7 or 1 days). Normal same-day reruns skip successful deliveries. Failed sends stay queued with backoff, and one account's failure does not stop the others. Updated expiry dates create a new delivery key. Concurrent runs claim short-lived leases before sending each account's digest.

Configure a scheduler only when SMTP is ready and its cost is approved. Run:

```sh
python manage.py cleanup_documents
python manage.py send_expiry_reminders
```

A Render daily cron expression of `0 1 * * *` runs at 09:00 Asia/Taipei. Use the same database, secret key, timezone, SMTP settings and Cloudinary settings as the web service. Failed deliveries retry on a later invocation; for more frequent retries, choose an appropriate hourly schedule. A missed scheduler run can miss an exact-day trigger. Successful SMTP acceptance followed by a process crash before the database acknowledgement can still cause a retry: SMTP does not offer an atomic transaction with the database.

Preview eligibility without sending or writing records:

```sh
python manage.py send_expiry_reminders --dry-run
```

### Private documents and deletion

PDF/image/Office uploads are stored as Cloudinary **authenticated raw** assets with opaque names. The app never renders their origin URL. The `documents/<item-id>/download/` route checks login and ownership, fetches the signed source on the server, and responds as an attachment with `Cache-Control: private, no-store`. Local development uses private filesystem storage with no public `/media/` route.

On deployment, `secure_existing_documents` converts referenced legacy files and app-tagged legacy assets under `media/documents/` to authenticated delivery. The conversion preserves bytes and uses a stable destination so a retry can recover after a successful provider rename. Previously shared public links are revoked; CDN invalidation is requested. Legacy files without a matching database item are preserved privately for operator review, not automatically erased.

A committed item deletion, cleared attachment, replaced attachment or account deletion queues removal of the stored file. The app attempts deletion after commit, retaining failed jobs for retry with provider cache invalidation. Rolled-back database changes keep the file. Run `python manage.py cleanup_documents` to retry pending work; startup also processes a bounded batch. Until a retry succeeds, an orphaned file can remain in private provider storage even though the application's download route no longer exposes it.

The form enforces 10 MB and matches declared MIME types to approved extensions; it does not scan for malware. Private access controls do not provide end-to-end encryption, erase a copy somebody already downloaded, or eliminate provider backup/cache retention. The public **Privacy & data** page describes these boundaries.

## Verification

```sh
python manage.py test --settings=config.test_settings
python manage.py makemigrations --check --dry-run --settings=config.test_settings
python -m pip check
```

Tests use an isolated in-memory database, storage and email backend. They cover registration, login, reset tokens, item CRUD, ownership boundaries, file limits, optional/retained/cleared attachments, date buckets, and reminder behavior. No schema changes are needed for these fixes.
