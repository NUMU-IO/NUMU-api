"""Resend webhook handler for receiving emails.

URL: POST /api/v1/webhooks/resend

Handles the `email.received` event from Resend's email receiving feature.
When an email arrives at *@numueg.app, Resend sends a webhook with metadata.
We fetch the full email content via the Resend API and forward it to the admin.

Setup required:
1. Add MX records for numueg.app pointing to Resend (see Resend dashboard → Receiving)
2. Register webhook URL in Resend dashboard → Webhooks → email.received
3. Set RESEND_WEBHOOK_SECRET in .env (from Resend dashboard → Webhooks → signing secret)
4. Set RESEND_FORWARD_TO in .env (email address to forward received emails to)
"""

import hashlib
import hmac
import logging

import resend
from fastapi import APIRouter, HTTPException, Request, status

from src.api.dependencies.services import get_email_service
from src.config import settings
from src.core.interfaces.services.email_service import EmailMessage

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_signature(payload: bytes, signature: str | None) -> bool:
    """Verify the Resend webhook signature using the signing secret."""
    if not settings.resend_webhook_secret:
        logger.warning("resend_webhook_no_secret: skipping signature verification")
        return True
    if not signature:
        return False
    expected = hmac.new(
        settings.resend_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("", operation_id="resend_webhook")
async def resend_webhook(request: Request):
    """Handle Resend webhook events (email.received)."""
    body = await request.body()
    signature = request.headers.get("resend-signature") or request.headers.get(
        "svix-signature"
    )

    if not _verify_signature(body, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    payload = await request.json()
    event_type = payload.get("type", "")

    if event_type != "email.received":
        # Acknowledge but ignore other event types
        return {"status": "ignored", "type": event_type}

    data = payload.get("data", {})
    email_id = data.get("email_id")
    from_addr = data.get("from", "unknown")
    to_addrs = data.get("to", [])
    subject = data.get("subject", "(no subject)")

    logger.info(
        "resend_email_received",
        email_id=email_id,
        from_addr=from_addr,
        to=to_addrs,
        subject=subject,
    )

    if not email_id:
        logger.warning("resend_webhook_no_email_id: cannot fetch full email")
        return {"status": "ok", "detail": "no email_id to forward"}

    # Fetch full email content from Resend API
    try:
        if settings.resend_api_key:
            resend.api_key = settings.resend_api_key

        email_data = resend.Emails.get(email_id)
        html_body = getattr(email_data, "html", None) or ""
        text_body = getattr(email_data, "text", None) or ""
    except Exception:
        logger.exception("resend_fetch_email_failed", email_id=email_id)
        # Still acknowledge the webhook so Resend doesn't retry
        return {"status": "ok", "detail": "fetch failed, will retry manually"}

    # Forward to admin
    forward_to = settings.resend_forward_to
    if not forward_to:
        logger.warning("resend_no_forward_to: RESEND_FORWARD_TO not configured")
        return {"status": "ok", "detail": "no forward address configured"}

    try:
        email_service = get_email_service()
        to_display = (
            ", ".join(to_addrs) if isinstance(to_addrs, list) else str(to_addrs)
        )

        forward_html = f"""
        <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px">
          <div style="background:#f0f4ff;border:1px solid #dbeafe;border-radius:12px;padding:16px;margin-bottom:20px">
            <p style="margin:0 0 4px;font-size:13px;color:#6b7280"><strong>From:</strong> {from_addr}</p>
            <p style="margin:0 0 4px;font-size:13px;color:#6b7280"><strong>To:</strong> {to_display}</p>
            <p style="margin:0;font-size:13px;color:#6b7280"><strong>Subject:</strong> {subject}</p>
          </div>
          <div style="border-top:1px solid #e5e7eb;padding-top:16px">
            {html_body or f'<pre style="white-space:pre-wrap;font-family:sans-serif;color:#374151">{text_body}</pre>'}
          </div>
        </div>
        """

        await email_service.send_email(
            EmailMessage(
                to=forward_to,
                subject=f"[Fwd] {subject}",
                html_content=forward_html,
                text_content=text_body or None,
                reply_to=from_addr if isinstance(from_addr, str) else None,
            )
        )

        logger.info(
            "resend_email_forwarded",
            email_id=email_id,
            forward_to=forward_to,
        )
    except Exception:
        logger.exception("resend_forward_failed", email_id=email_id)

    return {"status": "ok"}
