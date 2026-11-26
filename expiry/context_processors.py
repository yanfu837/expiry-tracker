"""Public account-help details; never expose settings or mail credentials."""
from urllib.parse import quote

from django.conf import settings


def account_support(request):
    email = settings.SUPPORT_EMAIL
    return {
        "support_email": email,
        "support_mailto": "mailto:"+quote(email, safe="@.+-") if email else "",
        "password_reset_minutes": settings.PASSWORD_RESET_TIMEOUT // 60,
    }
