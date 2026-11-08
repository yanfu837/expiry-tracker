"""A narrowly scoped GitHub Actions OIDC endpoint; never expose customer data."""
import json

import jwt
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .reminders import email_is_configured, reminder_preview, send_reminders

ISSUER = "https://token.actions.githubusercontent.com"
# Fetch only GitHub's fixed HTTPS key endpoint. Unknown key IDs are rejected
# without forcing an extra network fetch; refresh the cached set every 5 minutes.
github_keys = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks", lifespan=300, timeout=3)


def verify_workflow_token(token):
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or len(header["kid"]) > 128:
        raise jwt.InvalidTokenError()
    key = next((key for key in github_keys.get_signing_keys() if key.key_id == header["kid"]), None)
    if key is None or key.key_type != "RSA":
        raise jwt.InvalidTokenError()
    claims = jwt.decode(
        token, key.key, algorithms=["RS256"], issuer=ISSUER,
        audience=settings.REMINDER_OIDC_AUDIENCE, leeway=15,
        options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "repository_id",
                             "repository_owner_id", "repository", "ref", "event_name", "workflow_ref"]},
    )
    workflow = settings.REMINDER_OIDC_WORKFLOW_REF
    repository = workflow.split("/.github/workflows/", 1)[0]
    ref = workflow.rsplit("@", 1)[-1]
    owner, repo = repository.split("/", 1)
    # GitHub changed the default subject format for repositories created after
    # July 15, 2026. Both documented formats still require the immutable IDs.
    subjects = {
        f"repo:{repository}:ref:{ref}",
        f"repo:{owner}@{settings.REMINDER_OIDC_OWNER_ID}/{repo}@{settings.REMINDER_OIDC_REPOSITORY_ID}:ref:{ref}",
    }
    if (
        claims["repository_id"] != settings.REMINDER_OIDC_REPOSITORY_ID
        or claims["repository_owner_id"] != settings.REMINDER_OIDC_OWNER_ID
        or claims["repository"] != repository
        or claims["workflow_ref"] != workflow
        or claims["ref"] != ref
        or claims["sub"] not in subjects
        or claims["event_name"] not in {"schedule", "workflow_dispatch"}
    ):
        raise jwt.InvalidTokenError()


@csrf_exempt  # Machine requests authenticate with a signed, short-lived JWT.
@never_cache
@require_POST
def run_scheduled_reminders(request):
    if not all((settings.REMINDER_OIDC_AUDIENCE, settings.REMINDER_OIDC_REPOSITORY_ID,
                settings.REMINDER_OIDC_OWNER_ID, settings.REMINDER_OIDC_WORKFLOW_REF)):
        return JsonResponse({"error": "scheduler_not_configured"}, status=503)
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 8192:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        verify_workflow_token(authorization[7:])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return JsonResponse({"error": "forbidden"}, status=403)
    except (jwt.PyJWKClientError, jwt.PyJWKError, jwt.PyJWKSetError):
        return JsonResponse({"error": "identity_provider_unavailable"}, status=503)
    try:
        body = request.read(257)
        if len(body) > 256:
            raise ValueError()
        options = json.loads(body or b"{}")
        if (not isinstance(options, dict) or set(options) - {"dry_run"}
                or type(options.get("dry_run", False)) is not bool):
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_request"}, status=400)
    if options.get("dry_run", False):
        return JsonResponse({"dry_run": True, **reminder_preview()})
    if not email_is_configured():
        return JsonResponse({"error": "email_not_configured"}, status=503)
    # The caller can trigger existing reminders, but cannot choose a recipient,
    # change dates, read item details, or bypass the durable delivery records.
    return JsonResponse(send_reminders(limit=1000, max_accounts=1))
