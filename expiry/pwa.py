"""Public install metadata and an offline page containing no account information."""
import hashlib
import json

from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.http import require_safe


@require_safe
def manifest(request):
    response = JsonResponse({
        "id": "/", "name": "Expiry Tracker", "short_name": "Expiry Tracker",
        "description": "Track expiry dates and manage your important documents.",
        "lang": "en", "dir": "ltr", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#f4f7f9", "theme_color": "#075b76",
        "icons": [
            {"src": static("expiry/icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("expiry/icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("expiry/icons/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, content_type="application/manifest+json")
    response["Cache-Control"] = "public, max-age=0, must-revalidate"
    return response


@require_safe
def service_worker(request):
    assets = ["/offline/", static("expiry/styles.css"), static("expiry/pwa.js"),
              static("expiry/icons/icon-192.png"), static("expiry/icons/icon-512.png"),
              static("expiry/icons/icon-maskable-512.png"), static("expiry/icons/apple-touch-icon.png")]
    # Hashed production asset URLs change this version after each asset update.
    version = hashlib.sha256(json.dumps(assets).encode()).hexdigest()[:12]
    response = render(request, "expiry/service-worker.js", {
        "public_assets": assets, "cache_version": "v1-"+version,
    }, content_type="text/javascript")
    response["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    response["Service-Worker-Allowed"] = "/"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_safe
def offline(request):
    # Deliberately standalone: never render account navigation or private data.
    response = render(request, "expiry/offline.html")
    response["Cache-Control"] = "public, max-age=0, must-revalidate"
    return response
