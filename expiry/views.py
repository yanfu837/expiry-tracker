import logging
from datetime import timedelta
from smtplib import SMTPException

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import AccountEmailForm, ExpiryItemForm, RegistrationForm
from .models import ExpiryItem

logger = logging.getLogger(__name__)


def register(request):
    form = RegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("dashboard")
    return render(request, "expiry/register.html", {"form": form})


@login_required
def dashboard(request):
    groups = [[], [], [], []]
    for item in ExpiryItem.objects.filter(user=request.user).order_by("expiry_date", "name", "pk"):
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
        "sections": sections,
        "expired_items": groups[0],
        "within_30_days": groups[1],
        "within_90_days": groups[2],
        "later_items": groups[3],
    })


@login_required
def add_item(request):
    form = ExpiryItemForm(request.POST if request.method == "POST" else None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.user = request.user
        item.save()
        messages.success(request, "Expiry item added.")
        return redirect("dashboard")
    return render(request, "expiry/add_item.html", {"form": form})


@login_required
def edit_item(request, item_id):
    item = get_object_or_404(ExpiryItem, id=item_id, user=request.user)
    form = ExpiryItemForm(
        request.POST if request.method == "POST" else None,
        request.FILES or None,
        instance=item,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Expiry item updated.")
        return redirect("dashboard")
    return render(request, "expiry/edit_item.html", {"form": form, "item": item})


@login_required
def delete_item(request, item_id):
    item = get_object_or_404(ExpiryItem, id=item_id, user=request.user)
    if request.method == "POST":
        item.delete()
        messages.success(request, "Expiry item deleted.")
        return redirect("dashboard")
    return render(request, "expiry/delete_item.html", {"item": item})


@login_required
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
    except (SMTPException, OSError):
        logger.exception("Unable to send an expiry reminder")
        messages.error(request, "The reminder could not be sent. Please try again later or contact support.")
    else:
        messages.success(request, "Expiry reminder summary sent.")
    return redirect("dashboard")
