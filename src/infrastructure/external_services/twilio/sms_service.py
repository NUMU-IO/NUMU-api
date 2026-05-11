"""Twilio SMS service — Phase 8.6 stub.

Sends SMS via Twilio's HTTP API when credentials are present.
Without credentials, falls back to a logged no-op so campaigns can
be drafted + scheduled on stores that haven't configured Twilio yet
(the runner reports zero deliveries instead of crashing).

Credentials read from env vars at construction time:
    TWILIO_ACCOUNT_SID
    TWILIO_AUTH_TOKEN
    TWILIO_FROM             # e.g. "+15551234567" or alphanumeric sender ID

Per-store credential overrides happen via the `Store.settings.twilio`
JSONB block (mirrors how Paymob / Kashier / Fawry credentials are
stored). Service constructor resolves env → store-settings override.

NO message persistence here — the campaign runner records send
attempts in `message_log` (existing table for transactional outgoing
messages). This service is just the wire.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SMSDeliveryResult:
    """One SMS dispatch outcome."""

    success: bool
    # Twilio's message SID on success; an error description otherwise.
    provider_id: str | None = None
    error: str | None = None


class TwilioSMSService:
    def __init__(
        self,
        *,
        account_sid: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
    ) -> None:
        # Env fallback so a service can be constructed without args
        # (e.g. from a Celery task that doesn't know per-store config).
        # The campaign runner passes per-store overrides when present.
        self.account_sid = account_sid or os.environ.get("TWILIO_ACCOUNT_SID")
        self.auth_token = auth_token or os.environ.get("TWILIO_AUTH_TOKEN")
        self.from_number = from_number or os.environ.get("TWILIO_FROM")

    @property
    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)

    async def send(
        self,
        *,
        to: str,
        body: str,
    ) -> SMSDeliveryResult:
        """Dispatch a single SMS. Returns delivery outcome — never
        raises (campaign runner handles per-recipient failures by
        incrementing failed_count, not aborting the whole sweep).
        """
        if not self.is_configured:
            # Stub mode — log and return success=False with a
            # non-error reason so the runner records zero deliveries.
            logger.info(
                "twilio_stub_send",
                extra={"to": to, "body_length": len(body)},
            )
            return SMSDeliveryResult(
                success=False,
                error="Twilio not configured for this store",
            )

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        data = {"To": to, "From": self.from_number, "Body": body}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    url,
                    data=data,
                    auth=(self.account_sid, self.auth_token),
                )
            if res.status_code in (200, 201):
                payload: dict[str, Any] = res.json()
                return SMSDeliveryResult(
                    success=True, provider_id=payload.get("sid")
                )
            return SMSDeliveryResult(
                success=False,
                error=f"Twilio HTTP {res.status_code}: {res.text[:200]}",
            )
        except httpx.HTTPError as exc:
            return SMSDeliveryResult(success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — catch-all keeps the sweep alive
            logger.exception(
                "twilio_send_unexpected_error",
                extra={"to": to, "error": str(exc)},
            )
            return SMSDeliveryResult(success=False, error=f"Unexpected: {exc}")
