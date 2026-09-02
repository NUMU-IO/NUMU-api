"""Shared bot / throwaway-address defences for the public signup doors.

These checks were written for ``POST /public/demo/start`` and lived
inside that route. The registration endpoint had neither — which had it
backwards: a demo tenant is deleted after seven days, while registration
mints a permanent tenant, a subdomain, a Cloudflare DNS record and a
Search Console sitemap ping. The better-defended door should be the one
that costs us something.

Turnstile verification is skipped entirely when no secret is configured
(local and CI). When a secret *is* set it fails closed — a token that
cannot be verified, for any reason including Cloudflare being
unreachable, is rejected. That is the pre-existing behaviour of the demo
endpoint and is left as-is deliberately: quietly turning a captcha into
a no-op during an outage is not a change to make as a side effect of
moving the code. It does mean a Cloudflare outage closes the top of the
funnel, which is why ``require_turnstile`` is per-endpoint and off for
registration.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from src.config import settings

logger = logging.getLogger(__name__)

# Throwaway inbox providers. Deliberately short and hand-maintained
# rather than a downloaded list of thousands: every false positive here
# is a real merchant told their email is fake, and the long tail of
# disposable domains is better handled by requiring a verified phone.
DISPOSABLE_EMAIL_DOMAINS = frozenset({
    "mailinator.com",
    "tempmail.com",
    "10minutemail.com",
    "guerrillamail.com",
    "throwaway.email",
    "yopmail.com",
    "trashmail.com",
    "sharklasers.com",
    "getnada.com",
    "fakeinbox.com",
})

_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def is_disposable_email(email: str) -> bool:
    """True when *email* is at a known throwaway inbox provider."""
    domain = email.lower().rsplit("@", 1)[-1] if "@" in email else ""
    return domain in DISPOSABLE_EMAIL_DOMAINS


async def verify_turnstile_token(token: str | None, remote_ip: str | None) -> bool:
    """Verify a Cloudflare Turnstile token.

    Returns True when no secret is configured (development). With a
    secret set, returns False for a missing token and False on any
    transport failure — see the module docstring.
    """
    secret = getattr(settings, "turnstile_secret_key", None)
    if not secret:
        return True
    if not token:
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                _TURNSTILE_VERIFY_URL,
                data={
                    "secret": secret,
                    "response": token,
                    **({"remoteip": remote_ip} if remote_ip else {}),
                },
            )
        return bool(resp.json().get("success"))
    except Exception:
        logger.exception("turnstile_verify_failed")
        return False


async def guard_public_signup(
    *,
    email: str,
    turnstile_token: str | None,
    http_request: Request | None,
    require_turnstile: bool = True,
) -> None:
    """Run both checks for a public, unauthenticated signup.

    Raises 422 with a merchant-readable message on rejection. The two
    messages are deliberately different so support can tell from a
    screenshot which check fired.

    ``require_turnstile=False`` runs the throwaway-inbox check alone. The
    register endpoint uses it until the landing page is shipping a token:
    enforcing a captcha the client never sends would 422 every signup.
    """
    if is_disposable_email(email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please use a real email address.",
        )

    if not require_turnstile:
        return

    remote_ip = None
    if http_request is not None and http_request.client is not None:
        remote_ip = http_request.client.host

    if not await verify_turnstile_token(turnstile_token, remote_ip):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bot verification failed. Please try again.",
        )
