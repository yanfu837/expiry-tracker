# Mobile website and PWA

Expiry Tracker remains one Django website. Mobile browsers, an installed home-screen window and desktop browsers use the same accounts, permissions and database. No native app or new hosting service is required.

## Mobile use

- Log in, search item names or notes, and filter dates by expired, within 30 days, 31–90 days or later.
- Add or edit items, choose supported files with the phone's file picker, and download private attachments through the existing ownership checks.
- Forms use native date inputs, readable text and touch targets. On phones, bottom navigation links to Items, Add item and Email settings. Desktop keeps its wider card layout.
- Empty files and files above 10 MB are rejected in the browser before form upload, with an option to choose another file or save without an attachment. Server validation remains in place.
- Interface text is English. Customer-entered text is displayed as entered.

## Install on a home screen

Use the production HTTPS site. Installation availability depends on the browser.

- **Android / Chrome:** use the Install Expiry Tracker button when offered, or the browser menu → Install app / Add to Home screen.
- **iPhone / iPad:** open the website in Safari → Share → Add to Home Screen. Enable Open as Web App when offered.
- **Desktop Chrome / Edge:** use the address-bar install action when offered.

You may need to log in again in the installed window. This uses the same account and data; it does not create another account. Installing does not turn on SMTP, change reminder schedules or enable push notifications.

## Offline behavior and privacy

An internet connection is required to view or change private data. The service worker caches only an explicit list of public assets and a generic offline page. Account HTML, search results, reset links, uploaded files, downloads, APIs and POST requests are never added to Cache Storage. Private account pages also send `Cache-Control: no-store`.

If navigation fails while offline, the app shows a generic reconnect screen. It does not queue uploads or changes, and does not display a previously cached customer's list. Closing or refreshing an unsaved form can lose those edits.

The service worker lives at `/service-worker.js` and controls `/`. Its response is revalidated; production hashed asset URLs change the public cache version. Old caches with this app's prefix are removed on activation. Bump the worker version in `expiry/pwa.py` if changing the offline shell without changing any asset URLs.

## Deployment and validation

This change can be deployed independently of the self-hosted scanning and batch-import work in PR #4. It needs no database migration, new secrets or paid resources. Existing upload availability still follows the site's storage configuration. Private scanning is a separate pending change.

Run `python manage.py test --settings=config.test_settings`. Browser checks cover phone and desktop layouts, login, date filters, upload/edit flows, manifest assets, service-worker registration, offline navigation, reconnecting and the public-only cache contents. A headless browser cannot verify the final iOS home-screen installation interaction; test that on a real phone after HTTPS deployment.
