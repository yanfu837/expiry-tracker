import logging
from pathlib import PurePosixPath
import mimetypes
from datetime import timedelta
from smtplib import SMTPException

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe
from django.views.decorators.cache import never_cache
from django.utils.text import slugify
from django.views.decorators.cache import never_cache

from .forms import AccountEmailForm, DocumentAnalysisForm, ExpiryItemForm, RegistrationForm
from .document_processing import DocumentProcessingError, field_suggestions, make_scan_token, process_upload, processor_configured
from .models import ExpiryItem
from .storage import DocumentStorageError
from .reminders import email_is_configured

logger = logging.getLogger(__name__)


def register(request):
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("dashboard")
    return render(request, "expiry/register.html", {"form": form})


@login_required
@never_cache
def dashboard(request):
    query = request.GET.get("q", "").strip()[:200]
    window = request.GET.get("window", "all")
    if window not in {"all", "expired", "30", "90", "later"}:
        window = "all"
    items = ExpiryItem.objects.filter(user=request.user)
    if query:
        items = items.filter(Q(name__icontains=query) | Q(notes__icontains=query))
    today = timezone.localdate()
    if window == "expired":
        items = items.filter(expiry_date__lt=today)
    elif window == "30":
        items = items.filter(expiry_date__gte=today, expiry_date__lte=today+timedelta(days=30))
    elif window == "90":
        items = items.filter(expiry_date__gt=today+timedelta(days=30), expiry_date__lte=today+timedelta(days=90))
    elif window == "later":
        items = items.filter(expiry_date__gt=today+timedelta(days=90))
    groups = [[], [], [], []]
    for item in items.order_by("expiry_date", "name", "pk"):
        days = item.days_remaining
        index = 0 if days < 0 else 1 if days <= 30 else 2 if days <= 90 else 3
        groups[index].append(item)
    sections = [
        {"id": "expired", "title": "Expired", "items": groups[0], "empty": "No expired items."},
        {"id": "thirty", "title": "Within 30 days", "items": groups[1], "empty": "No items expiring within 30 days."},
        {"id": "ninety", "title": "31–90 days", "items": groups[2], "empty": "No items expiring in 31–90 days."},
        {"id": "later", "title": "Later", "items": groups[3], "empty": "No later items."},
    ]
    return render(request, "expiry/dashboard.html", {
        "sections": sections, "query": query, "window": window,
        "has_filters": bool(query or window != "all"), "total_items": sum(map(len, groups)),
        "expired_items": groups[0],
        "within_30_days": groups[1],
        "within_90_days": groups[2],
        "later_items": groups[3],
        "email_available": email_is_configured(),
    })


@login_required
@never_cache
def add_item(request):
    form = ExpiryItemForm(request.POST if request.method == "POST" else None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.user = request.user
        try:
            item.save()
        except (DocumentStorageError, DocumentProcessingError):
            form.add_error("document", "The document could not be safely saved. Please try again later.")
        else:
            messages.success(request, "Expiry item added.")
            return redirect("dashboard")
    return render(request, "expiry/add_item.html", {"form": form, "processor_available": processor_configured()})


@login_required
@never_cache
def edit_item(request, item_id):
    item = get_object_or_404(ExpiryItem, id=item_id, user=request.user)
    form = ExpiryItemForm(
        request.POST if request.method == "POST" else None,
        request.FILES or None,
        instance=item,
        user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except (DocumentStorageError, DocumentProcessingError):
            form.add_error("document", "The document could not be safely saved. Please try again later.")
        else:
            messages.success(request, "Expiry item updated.")
            return redirect("dashboard")
    return render(request, "expiry/edit_item.html", {"form": form, "item": item, "processor_available": processor_configured()})


@login_required
@never_cache
@require_POST
def analyze_document(request):
    form = DocumentAnalysisForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({"error": "invalid_file", "message": "Choose a supported document, up to 10 MB."}, status=400)
    document = form.cleaned_data["document"]
    try:
        payload = process_upload(document, extract=True)
        result = field_suggestions(payload, document.name)
        return JsonResponse({"fields": result, "scan_token": make_scan_token(document, request.user.pk)})
    except DocumentProcessingError as error:
        status = 422 if error.code in {"infected", "invalid_file"} else 503
        return JsonResponse({"error": error.code, "message": str(error)}, status=status)
    except (TypeError, ValueError, AttributeError):
        return JsonResponse({"error": "unavailable", "message": "The extracted result could not be used. Please try again later."}, status=503)


@login_required
@never_cache
def delete_item(request, item_id):
    item = get_object_or_404(ExpiryItem, id=item_id, user=request.user)
    if request.method == "POST":
        item.delete()
        messages.success(request, "Expiry item deleted.")
        return redirect("dashboard")
    return render(request, "expiry/delete_item.html", {"item": item})


@login_required
@require_safe
def download_document(request, item_id):
    item = get_object_or_404(ExpiryItem, pk=item_id, user=request.user)
    if not item.document:
        raise Http404
    try:
        file = item.document.open("rb")
    except (OSError, ValueError):
        logger.warning("Document download unavailable for item %s", item.pk)
        response = HttpResponse("This document is temporarily unavailable. Please try again later.", status=503)
        response["Retry-After"] = "60"
    else:
        extension = PurePosixPath(item.document.name).suffix.lower()
        filename = (slugify(item.name)[:100] or "document") + extension
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = FileResponse(file, as_attachment=True, filename=filename, content_type=content_type)
    response["Cache-Control"] = "private, no-store, max-age=0"
    response["Pragma"] = "no-cache"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@never_cache
def account_email(request):
    form = AccountEmailForm(request.POST if request.method == "POST" else None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your email address has been updated.")
        return redirect("dashboard")
    return render(request, "expiry/account_email.html", {"form": form})


@login_required
@require_POST
def test_email(request):
    if not email_is_configured():
        messages.error(request, "Email reminders are not enabled yet.")
        return redirect("dashboard")
    if not request.user.email:
        messages.error(request, "Add an email address before sending reminders.")
        return redirect("account_email")
    today = timezone.localdate()
    items = list(ExpiryItem.objects.filter(
        user=request.user,
        expiry_date__gte=today,
        expiry_date__lte=today + timedelta(days=30),
    ).order_by("expiry_date"))
    if not items:
        messages.info(request, "No items expiring within 30 days.")
        return redirect("dashboard")
    message = "You have items expiring within 30 days:\n\n" + "\n".join(
        f"- {item.name} — {(item.expiry_date - today).days} days remaining\n  Expiry date: {item.expiry_date}\n"
        for item in items
    )
    try:
        send_mail(f"Expiry Tracker: {len(items)} items expiring soon", message, None, [request.user.email])
    except (SMTPException, OSError) as error:
        logger.error("Unable to send an expiry reminder for account %s (%s)", request.user.pk, type(error).__name__)
        messages.error(request, "The reminder could not be sent. Please try again later or contact support.")
    else:
        messages.success(request, "Expiry reminder summary sent.")
    return redirect("dashboard")


class ConfiguredPasswordResetView(auth_views.PasswordResetView):
    def dispatch(self, request, *args, **kwargs):
        if not email_is_configured():
            response = render(request, "expiry/email_unavailable.html", status=503)
            response["Cache-Control"] = "private, no-store"
            response["Retry-After"] = "3600"
            return response
        return super().dispatch(request, *args, **kwargs)


def privacy(request):
    return render(request, "expiry/privacy.html")
