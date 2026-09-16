from django.shortcuts import render, redirect, get_object_or_404
from .models import ExpiryItem
from .forms import ExpiryItemForm
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.http import HttpResponse
from datetime import date, timedelta


def register(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)

        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("dashboard")

    else:
        form = UserCreationForm()

    return render(
        request,
        "expiry/register.html",
        {
            "form": form
        }
    )

@login_required

def dashboard(request):
    items = ExpiryItem.objects.filter(user=request.user)

    expired_items = []
    within_30_days = []
    within_90_days = []
    later_items = []

    for item in items:
        days = item.days_remaining

        if days < 0:
            expired_items.append(item)
        elif days <= 30:
            within_30_days.append(item)
        elif days <= 90:
            within_90_days.append(item)
        else:
            later_items.append(item)

    return render(
        request,
        "expiry/dashboard.html",
        {
            "expired_items": expired_items,
            "within_30_days": within_30_days,
            "within_90_days": within_90_days,
            "later_items": later_items,
        }
    )

@login_required
def add_item(request):
    if request.method == "POST":
        form = ExpiryItemForm(request.POST,request.FILES)

        if form.is_valid():
            item = form.save(commit=False)
            item.user = request.user
            item.save()
            return redirect("dashboard")

    else:
        form = ExpiryItemForm()

    return render(
        request,
        "expiry/add_item.html",
        {
            "form": form
        }
    )
@login_required
def edit_item(request, item_id):
    item = get_object_or_404(
        ExpiryItem,
        id=item_id,
        user=request.user
    )

    if request.method == "POST":
        form = ExpiryItemForm(request.POST, request.FILES, instance=item)

        if form.is_valid():
            form.save()
            return redirect("dashboard")
    else:
        form = ExpiryItemForm(instance=item)

    return render(
        request,
        "expiry/edit_item.html",
        {
            "form": form,
            "item": item,
        }
    )

@login_required
def delete_item(request, item_id):
    item = get_object_or_404(
        ExpiryItem,
        id=item_id,
        user=request.user
    )

    if request.method == "POST":
        item.delete()
        return redirect("dashboard")

    return render(
        request,
        "expiry/delete_item.html",
        {
            "item": item
        }
    )
@login_required
def test_email(request):

    today = date.today()
    thirty_days_later = today + timedelta(days=30)

    items = ExpiryItem.objects.filter(
        user=request.user,
        expiry_date__gte=today,
        expiry_date__lte=thirty_days_later
    ).order_by("expiry_date")

    if not items.exists():
        return HttpResponse("No items expiring within 30 days.")

    message = "You have items expiring within 30 days:\n\n"

    for item in items:
        days_remaining = (item.expiry_date - today).days

        message += (
            f"- {item.name} — {days_remaining} days remaining\n"
            f"  Expiry date: {item.expiry_date}\n\n"
        )

    subject = f"Expiry Tracker: {items.count()} items expiring soon"

    send_mail(
        subject,
        message,
        "test@example.com",
       [request.user.email],
        fail_silently=False,
    )

    return HttpResponse("Expiry reminder summary sent.")

    today = date.today()
    thirty_days_later = today + timedelta(days=30)

    item = ExpiryItem.objects.filter(
        user=request.user,
        expiry_date__gte=today,
        expiry_date__lte=thirty_days_later
    ).order_by("expiry_date").first()

    if item is None:
        return HttpResponse("No items expiring within 30 days.")

    days_remaining = (item.expiry_date - today).days

    subject = f"Expiry reminder: {item.name}"

    message = (
        f"{item.name} will expire in {days_remaining} days.\n"
        f"Expiry date: {item.expiry_date}\n"
    )

    send_mail(
        subject,
        message,
           " Your Gmail@gmail.com",
        [request.user.email],
        fail_silently=False,
    )

    return HttpResponse("Expiry reminder test sent.")