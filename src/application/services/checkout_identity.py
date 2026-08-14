"""Phone-first checkout identity — shared capability + verified-flag helpers.

The feature has three consumers that must agree with each other:

- ``GET /checkout-config`` tells the storefront whether to render the OTP
  gate and the save-cart nudge (``identity.otp_available``).
- The identity routes (``/identity/otp/issue|verify``) issue codes and, on a
  successful verify, record the proof.
- The checkout endpoint enforces the proof server-side (the modal is UX,
  not the guard).

They agree by sharing the two functions here: ``otp_available`` is the single
definition of "this store can actually deliver an OTP", and the
``identity_flag_*`` helpers are the single definition of where a verify's
proof lives. Enforcement reading a different key than verify writes would be
a checkout outage, so both sides import from here.

## The verified flag

``identity_verified:{store_id}:{cart_key}`` → ``{"phone": <E.164>,
"verified_at": iso, "otp_id": str}``, TTL 24h.

Keyed by the CART (``CartOwner.cart_key`` — customer id when logged in, else
the ``numu_cart_session`` cookie id), not by the phone: the proof is "THIS
browser session demonstrated ownership of THIS phone", and checkout compares
the flag's phone against the shipping phone. 24h covers a same-day return to
an open checkout without re-verifying, while keeping the proof short enough
that a shared/abandoned device doesn't stay verified forever.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.config.settings import settings

logger = logging.getLogger(__name__)

# How long a successful verify keeps this cart session verified.
IDENTITY_FLAG_TTL_SECONDS = 24 * 3600

# Minimum gap between two OTP issues for the same phone (anti-spam +
# anti-cost; a legitimate "resend" click after the code didn't arrive).
OTP_RESEND_COOLDOWN_SECONDS = 45


def identity_flag_key(store_id: UUID | str, cart_key: str) -> str:
    return f"identity_verified:{store_id}:{cart_key}"


async def read_identity_flag(
    cache: Any, store_id: UUID | str, cart_key: str
) -> dict | None:
    """The verify proof for this cart session, or None when absent.

    Deliberately RAISES on a Redis outage instead of returning None —
    "unverified" and "cannot know" need different handling: the status
    route degrades to unverified, but checkout enforcement must fail OPEN
    (a Redis blip must never become a store-wide checkout outage). Uses the
    raw client because RedisCacheService.get swallows RedisError into None,
    which would erase exactly that distinction.
    """
    client = await cache._get_client()  # noqa: SLF001 — see docstring
    raw = await client.get(identity_flag_key(store_id, cart_key))
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


async def write_identity_flag(
    cache: Any,
    store_id: UUID | str,
    cart_key: str,
    *,
    phone_e164: str,
    otp_id: str,
) -> None:
    payload = json.dumps({
        "phone": phone_e164,
        "verified_at": datetime.now(UTC).isoformat(),
        "otp_id": otp_id,
    })
    await cache.set(
        identity_flag_key(store_id, cart_key),
        payload,
        expire=IDENTITY_FLAG_TTL_SECONDS,
    )


async def otp_available(
    store_id: UUID,
    store_settings: dict | None,
    db_session: Any,
) -> bool:
    """Whether this store's WhatsApp transport can deliver an OTP right now.

    v1 truth: **GOWA only.** GOWA sends the locally-rendered plain text, so
    nothing external gates it beyond a paired (or platform) device. Meta
    requires an approved AUTHENTICATION template with the special OTP
    component, which ``_build_template_message`` cannot emit yet — so a Meta
    store reads False here and the whole feature self-degrades to today's
    checkout, rather than showing a gate whose "send code" button 503s.
    (When the Meta AUTH path lands, this is the one function to widen.)

    Fail-closed: any resolution error means "no gate" — the failure mode of
    a wrong False is the feature quietly staying off for one store; a wrong
    True is a checkout customers cannot pass.
    """
    if not settings.checkout_identity_enabled:
        return False

    try:
        from src.infrastructure.external_services.whatsapp import (
            resolve_provider_name,
        )

        if resolve_provider_name(store_settings) != "gowa":
            return False

        from src.infrastructure.repositories.whatsapp_gowa_device_repository import (
            WhatsAppGowaDeviceRepository,
        )

        gowa_repo = WhatsAppGowaDeviceRepository(db_session)
        device = await gowa_repo.get_active_for_store(store_id)
        if device is None:
            device = await gowa_repo.get_platform_device()
        if device is None:
            return False
        # A logged-out/banned device can't send; mirror the GOWA guard's
        # health gate so the capability never promises what the send blocks.
        return device.status not in {"logged_out", "banned"}
    except Exception:
        logger.exception(
            "checkout_identity_capability_check_failed",
            extra={"store_id": str(store_id)},
        )
        return False
