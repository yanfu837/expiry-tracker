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

Visit <http://127.0.0.1:8000/>. Development uses local SQLite and local file uploads. Password reset emails are printed in the terminal with the example console mail backend; no real emails are sent.

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

Users can send an upcoming-items summary with **Email upcoming items**. This requires an explicit POST and uses the saved account email. To enable automatic reminders, configure an external daily scheduler to run:

```sh
python manage.py send_expiry_reminders
```

Use the same database and SMTP settings. The command sends reminders when items are exactly 30, 14, 7 or 1 day away, using `TIME_ZONE`. Run it once per day; scheduling and duplicate-run tracking are not configured by this repository.

### Attachments

For persistent uploads on Render, retain or configure all three Cloudinary credentials (`CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`), or use `CLOUDINARY_URL`. Files remain on the existing Cloudinary account. Without persistent storage configured on Render, new uploads show a validation message; records can still be saved without a file.

The form accepts the listed PDF/image/Office extensions, checks the declared MIME type against the extension, and enforces 10 MB. These metadata checks do not inspect file contents or scan for malware. Existing Cloudinary URLs retain their current access configuration; this change does not make previously public files private.

## Verification

```sh
python manage.py test --settings=config.test_settings
python manage.py makemigrations --check --dry-run --settings=config.test_settings
python -m pip check
```

Tests use an isolated in-memory database, storage and email backend. They cover registration, login, reset tokens, item CRUD, ownership boundaries, file limits, optional/retained/cleared attachments, date buckets, and reminder behavior. No schema changes are needed for these fixes.
