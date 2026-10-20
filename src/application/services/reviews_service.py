"""Reviews app «آراء العملاء»: the rules, with no I/O.

docs/Plans/APPS/03-reviews (§ Version 1.0). A few days after an order is
delivered the buyer gets one WhatsApp message; its button opens the store's
own ``/reviews/write`` page, where they rate what they bought without logging
in. The reviews are core ``product_reviews`` rows.
"""

from __future__ import annotations

import hashlib
import random
import secrets
from datetime import UTC, datetime, time, timedelta
from typing import Any

from src.core.utils.store_timezone import safe_zone

SLUG = "reviews"

# Request statuses.
SCHEDULED, SENT, COMPLETED = "scheduled", "sent", "completed"
SKIPPED, CANCELLED, FAILED = "skipped", "cancelled", "failed"

#: Order statuses that call a waiting request off.
CALL_OFF = frozenset({"returned", "refunded", "cancelled"})

#: Requests go out between 10:00 and 21:00 store time (R-3), spread over the
#: first half hour of the window so a morning's batch does not leave at once.
SEND_FROM, SEND_UNTIL = 10, 21
SPREAD_SECONDS = 1800
#: A request the sweep could not send for this long is dropped, not sent late.
SEND_LATE = timedelta(days=14)
#: The review link works this long after the message went out.
LINK_DAYS = 60

DELAY_MIN, DELAY_MAX = 1, 14
CAP_MAX = 5000
DEFAULT_SETTINGS: dict[str, Any] = {
    "requests_enabled": True,
    "delay_days": 3,
    "daily_cap": 300,
    "photos_enabled": True,
}

# Moderation, on core rows.
REJECT_REASONS = ("spam", "abuse", "personal", "off_topic", "duplicate")
TABS = ("pending", "published", "rejected", "spam")
MODES = ("auto", "manual", "auto_with_filter")

# The write form.
TITLE_MAX, BODY_MAX, NAME_MAX = 200, 4000, 120
PHOTOS_PER_REVIEW, PHOTOS_PER_POST = 5, 10
PHOTO_BYTES = 8 * 1024 * 1024
PHOTO_SIZE, THUMB_SIZE = 1600, 400


def merged_settings(row: Any | None) -> dict[str, Any]:
    if row is None:
        return dict(DEFAULT_SETTINGS)
    return {key: getattr(row, key) for key in DEFAULT_SETTINGS}


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; they are UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def in_window(now: datetime, tz_name: str) -> bool:
    return SEND_FROM <= now.astimezone(safe_zone(tz_name)).hour < SEND_UNTIL


def due_at(
    delivered: datetime,
    delay_days: int,
    tz_name: str,
    *,
    rand: Any = random.random,
) -> datetime:
    """``delay_days`` after delivery, moved into the next send window."""
    tz = safe_zone(tz_name)
    local = (delivered + timedelta(days=delay_days)).astimezone(tz)
    if SEND_FROM <= local.hour < SEND_UNTIL:
        return local.astimezone(UTC)
    day = local.date() if local.hour < SEND_FROM else local.date() + timedelta(days=1)
    start = datetime.combine(day, time(SEND_FROM), tzinfo=tz)
    return (start + timedelta(seconds=rand() * SPREAD_SECONDS)).astimezone(UTC)


def new_token() -> str:
    """128 bits, URL-safe; only its hash is stored."""
    return secrets.token_urlsafe(16)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def link_open(sent_at: datetime | None, now: datetime) -> bool:
    sent = aware(sent_at)
    return sent is not None and now - sent <= timedelta(days=LINK_DAYS)


def button_value(subdomain: str, token: str) -> str:
    """The button's suffix after ``https://numueg.app/a/``."""
    return f"{SLUG}/{subdomain}/{token}"


_FALLBACK_NAME = {"ar": "عميلنا", "en": "there"}


def template_params(
    first_name: str | None, order_number: str, store_name: str, button: str, lang: str
) -> dict[str, str]:
    """``{{1}}`` first name, ``{{2}}`` order number, ``{{3}}`` store; the
    button's suffix. Meta refuses an empty parameter."""
    return {
        "customer_name": (first_name or "").strip() or _FALLBACK_NAME[lang],
        "order_number": order_number,
        "store_name": store_name,
        "review_link": button,
    }


def tab_of(is_approved: bool, rejected_reason: str | None) -> str:
    if is_approved:
        return "published"
    if rejected_reason == "spam":
        return "spam"
    return "rejected" if rejected_reason else "pending"


def moderation(store_settings: dict | None) -> dict[str, Any]:
    """The store's ``review_moderation`` as the settings screen shows it."""
    raw = (store_settings or {}).get("review_moderation")
    mode = raw.get("mode") if isinstance(raw, dict) else raw
    words = raw.get("flagged_words") if isinstance(raw, dict) else None
    return {
        "moderation_mode": mode if mode in MODES else "auto",
        "flagged_words": [str(w) for w in words] if isinstance(words, list) else [],
    }
