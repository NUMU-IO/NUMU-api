"""Order Tracking (``order-tracking``, «مشوار»): the app's rules, with no I/O.

docs/Plans/APPS/02-order-tracking. The routes, the event handler and the tasks
decide through here, so each rule has one definition and one test: the
parcel's status and steps, attempts, the delivery window and "delayed", areas
and carriers, quiet hours, retry requests, the board and the 30-day analytics.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from src.application.services.carrier_registry import get_spec
from src.core.utils.store_timezone import local_date, safe_zone
from src.core.value_objects.geography import (
    get_governorate_by_code,
    resolve_governorate,
)

SLUG = "order-tracking"

OUT_FOR_DELIVERY, DELIVERY_FAILED = "out_for_delivery", "delivery_failed"
#: Message kind → (the shipment status that triggers it, its system template).
#: The kinds are also the ``MessageType`` values the templates map to.
KINDS: dict[str, tuple[str, str]] = {
    OUT_FOR_DELIVERY: ("out_for_delivery", "app_order_tracking_out_for_delivery_v1"),
    DELIVERY_FAILED: ("failed", "app_order_tracking_delivery_failed_v1"),
}
KIND_OF_STATUS = {status: kind for kind, (status, _t) in KINDS.items()}
SWITCH = {
    OUT_FOR_DELIVERY: "out_for_delivery_enabled",
    DELIVERY_FAILED: "failed_enabled",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "out_for_delivery_enabled": True,
    "failed_enabled": True,
    "daily_cap": 300,
    "show_items": True,
    "show_contact": True,
}
CAP_MAX = 5000

#: The board, the parcels list, the analytics and the reconcile look this far back.
WINDOW = timedelta(days=30)
#: Every app row is deleted this long after it was made.
KEEP = timedelta(days=180)
#: A retry note is blanked this long after the merchant handled the request.
NOTE_KEEP = timedelta(days=30)
#: One "Try again" per order in this time.
RETRY_EVERY = timedelta(hours=12)
RETRY_DAYS = 3
WINDOWS = ("morning", "afternoon", "evening")
NOTE_MAX = 200
#: A status change older than this is never messaged (TRK-I-08).
SEND_LATE = timedelta(hours=2)
#: Quiet hours in the store's time: a message due 22:00–09:00 waits for 09:00,
#: spread over the next half hour (NUMU's shared GOWA device takes 20 a minute).
QUIET_FROM, QUIET_UNTIL = 22, 9
QUIET_SPREAD_SECONDS = 1800
#: The zone promise when an order has no zone (``shipping_resolver.py``).
FALLBACK_DAYS = (2, 5)
#: An area or a carrier needs this many parcels to be ranked.
MIN_PARCELS = 10
QUEUE_MAX = 50

SCHEDULED, SENT, SKIPPED, FAILED = "scheduled", "sent", "skipped", "failed"

TILES = (
    "in_transit",
    "out_for_delivery",
    "delivered_today",
    "delayed",
    "failed",
    "returned",
)
CLOSED = frozenset({"delivered", "returned", "cancelled"})
#: Orders the shopper routes answer 404 for, like an unknown id.
HIDDEN_ORDER_STATUSES = frozenset({
    "draft",
    "awaiting_payment",
    "payment_failed",
    "pending_deposit",
})


def _v(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def hidden(order_status: Any) -> bool:
    """A draft or unpaid order: the shopper routes answer 404, as for no order."""
    return _v(order_status) in HIDDEN_ORDER_STATUSES


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; every one of ours is UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


# ─── Status and steps ──────────────────────────────────────────────────

#: Shipment statuses that say more than the order does.
_CARRIER_PROGRESS = frozenset({
    "picked_up",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "failed",
    "returned",
})
_ORDER_STATUS = {
    "pending": "pending",
    "confirmed": "confirmed",
    "processing": "processing",
    "shipped": "in_transit",
    "delivered": "delivered",
    "returned": "returned",
    "cancelled": "cancelled",
    "refunded": "cancelled",
}


def parcel_status(shipment_status: Any, order_status: Any) -> str:
    """The app's status: the carrier's progress, unless the order closed."""
    order = _ORDER_STATUS.get(_v(order_status), "pending")
    shipment = _v(shipment_status)
    if order in CLOSED or shipment not in _CARRIER_PROGRESS:
        return order
    return shipment


def _ts(raw: Any) -> datetime | None:
    try:
        return aware(datetime.fromisoformat(str(raw)))
    except (TypeError, ValueError):
        return None


_JOURNEY = frozenset({
    "picked_up",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "failed",
    "returned",
    "cancelled",
})


def journey(history: list[dict] | None) -> list[tuple[str, datetime | None]]:
    """The carrier's steps in order. A repeat in a row is one step; unknown
    statuses are dropped, and so is every description (it can carry carrier
    errors and merchant text)."""
    out: list[tuple[str, datetime | None]] = []
    for entry in history or []:
        key = _v(entry.get("to"))
        if key in _JOURNEY and (not out or out[-1][0] != key):
            out.append((key, _ts(entry.get("timestamp"))))
    return out


def entries(history: list[dict] | None, status: str) -> int:
    """How many times the shipment moved INTO ``status``. A carrier repeating
    the status it is already in (a re-sent webhook) is not a new attempt."""
    return sum(
        1
        for e in history or []
        if _v(e.get("to")) == status and _v(e.get("from")) != status
    )


def entered_at(history: list[dict] | None, status: str) -> datetime | None:
    """When the shipment last moved into ``status``."""
    for e in reversed(history or []):
        if _v(e.get("to")) == status and _v(e.get("from")) != status:
            return _ts(e.get("timestamp"))
    return None


def merchant_steps(
    order_status: Any,
    confirmed_at: datetime | None,
    history: list[dict] | None,
) -> list[dict[str, Any]]:
    """The drawer's timeline: confirmed (once the order passed it), then the
    carrier's journey with times."""
    steps = journey(history)
    if confirmed_at or _v(order_status) not in ("pending", *HIDDEN_ORDER_STATUSES):
        steps.insert(0, ("confirmed", confirmed_at))
    return [{"key": k, "at": _iso(at)} for k, at in steps]


LINE = ("confirmed", "picked_up", "in_transit", "out_for_delivery", "delivered")
_AT = {
    "pending": 0,
    "confirmed": 0,
    "processing": 0,
    "picked_up": 1,
    "in_transit": 2,
    "out_for_delivery": 3,
    "failed": 3,
    "delivered": 4,
}


def shopper_steps(
    status: str,
    confirmed_at: datetime | None,
    history: list[dict] | None,
) -> list[dict[str, Any]]:
    """The five steps of the shopper's route line (CONTRACT ``steps``).

    ``failed`` takes the place of ``out_for_delivery`` as the current step;
    ``returned`` and ``cancelled`` follow the last step reached as the
    current one, and the steps not reached stay ``ahead``.
    """
    times: dict[str, datetime | None] = {"confirmed": confirmed_at}
    for key, at in journey(history):
        times[key] = at
    if status in _AT:
        current = _AT[status]
        keys = list(LINE)
        if status == "failed":
            keys[3] = "failed"
        return [
            {
                "key": key,
                "state": "done"
                if i < current
                else "current"
                if i == current
                else "ahead",
                "at": _iso(times.get(key)) if i <= current else None,
            }
            for i, key in enumerate(keys)
        ]
    # returned / cancelled
    reached = {k for k, _ in journey(history)} | (
        {"confirmed"} if confirmed_at else set()
    )
    if "failed" in reached:
        reached.add("out_for_delivery")
    last = max((i for i, k in enumerate(LINE) if k in reached), default=-1)
    steps = [
        {
            "key": key,
            "state": "done" if i <= last else "ahead",
            "at": _iso(times.get(key)) if i <= last else None,
        }
        for i, key in enumerate(LINE)
    ]
    steps.insert(
        last + 1, {"key": status, "state": "current", "at": _iso(times.get(status))}
    )
    return steps


def _iso(value: datetime | None) -> str | None:
    value = aware(value)
    return value.isoformat() if value else None


# ─── Window, delay, area, carrier ──────────────────────────────────────


def eta_window(
    placed_at: datetime, tz_name: str, days: tuple[int, int] | None
) -> tuple[date, date]:
    """The promise the shopper saw at checkout: the zone's "X–Y days" from
    the day they ordered, in the store's time; 2–5 days without a zone."""
    low, high = days or FALLBACK_DAYS
    start = local_date(placed_at, tz_name)
    return start + timedelta(days=low), start + timedelta(days=max(low, high))


def is_delayed(status: str, eta_max: date | None, today: date) -> bool:
    """Open, and the last promised day is behind us (store time)."""
    return status not in CLOSED and eta_max is not None and eta_max < today


def area_of(address: dict | None) -> tuple[str | None, str | None]:
    """``(governorate ISO code, city)`` from an order's shipping address;
    never the street."""
    a = address or {}
    gov = None
    for key in ("governorate", "state", "city"):
        gov = resolve_governorate(str(a.get(key) or "").strip())
        if gov:
            break
    city = str(a.get("city") or "").strip()[:100] or None
    return (gov.code if gov else None), city


def area_out(code: str | None, city: str | None) -> dict[str, Any] | None:
    if not code and not city:
        return None
    gov = get_governorate_by_code(code) if code else None
    return {
        "governorate_code": code,
        "governorate_ar": gov.name_ar if gov else None,
        "governorate_en": gov.name_en if gov else None,
        "city": city,
    }


def carrier_out(key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    spec = get_spec(key)
    return {
        "key": key,
        "name_ar": spec.name_ar if spec else key,
        "name_en": spec.name_en if spec else key,
    }


# ─── Messages ──────────────────────────────────────────────────────────


def send_time(
    now: datetime,
    tz_name: str,
    *,
    spread_seconds: float = QUIET_SPREAD_SECONDS,
    rand: Any = random.random,
) -> datetime:
    """Now, or 09:00 store time (plus a random spread) when it is quiet hours."""
    tz = safe_zone(tz_name)
    local = now.astimezone(tz)
    if QUIET_UNTIL <= local.hour < QUIET_FROM:
        return now
    day = local.date() if local.hour < QUIET_UNTIL else local.date() + timedelta(days=1)
    nine = datetime.combine(day, time(QUIET_UNTIL), tzinfo=tz)
    return (nine + timedelta(seconds=rand() * spread_seconds)).astimezone(UTC)


def button_value(subdomain: str | None, order_id: Any) -> str:
    """The Track button's suffix after ``https://numueg.app/o/``: the
    self-describing ``<subdomain>/<order_id>`` core's buttons use."""
    return f"{subdomain}/{order_id}" if subdomain else str(order_id)


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
        "order_id": button,
    }


# ─── Retry requests ────────────────────────────────────────────────────

CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def retry_day_ok(day: date, today: date) -> bool:
    """Today, tomorrow or the day after, in the store's time."""
    return today <= day < today + timedelta(days=RETRY_DAYS)


_WINDOW_LABEL = {
    "morning": ("الصبح (9–12)", "morning (9–12)"),
    "afternoon": ("بعد الضهر (12–5)", "afternoon (12–5)"),
    "evening": ("بالليل (5–9)", "evening (5–9)"),
}


def retry_comment(day: date, window: str, note: str | None) -> str:
    """The order activity the merchant reads, in both languages."""
    ar, en = _WINDOW_LABEL[window]
    text = (
        f"العميل عايز محاولة توصيل تانية يوم {day.isoformat()} {ar}.\n"
        f"The customer asked for another delivery attempt on {day.isoformat()}, {en}."
    )
    return f"{text}\nملاحظة / Note: {note}" if note else text


def courier_note(existing: str | None, day: date, window: str, note: str | None) -> str:
    """The shipment's note for Bosta: what it already said, then the shopper's
    request. Bosta takes the whole field, so nothing already there is lost."""
    line = f"العميل عايز يستلم يوم {day.isoformat()} {_WINDOW_LABEL[window][0]}"
    if note:
        line = f"{line}: {note}"
    return f"{existing.rstrip()}\n{line}" if existing and existing.strip() else line


# ─── The board and the analytics ───────────────────────────────────────


@dataclass(frozen=True)
class Parcel:
    """One parcel as the merchant screens see it, read live from core."""

    order_id: Any
    order_number: str
    status: str
    carrier: str | None
    governorate_code: str | None
    city: str | None
    eta_min: date | None
    eta_max: date | None
    attempts: int
    failed_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    updated_at: datetime | None


def in_tile(p: Parcel, tile: str, today: date, tz_name: str) -> bool:
    if tile == "in_transit":
        return p.status in ("picked_up", "in_transit")
    if tile == "delayed":
        return is_delayed(p.status, p.eta_max, today)
    if tile == "delivered_today":
        return (
            p.status == "delivered"
            and p.delivered_at is not None
            and local_date(p.delivered_at, tz_name) == today
        )
    return p.status == tile


def board_counts(parcels: list[Parcel], today: date, tz_name: str) -> dict[str, int]:
    return {t: sum(in_tile(p, t, today, tz_name) for p in parcels) for t in TILES}


def queue(
    parcels: list[Parcel], open_retries: dict[Any, Any], today: date
) -> list[tuple[str, Parcel]]:
    """ "Needs you": open retry requests, then failed attempts, then delayed
    parcels; oldest first in each, at most ``QUEUE_MAX``."""
    asked = sorted(
        (p for p in parcels if p.order_id in open_retries),
        key=lambda p: aware(open_retries[p.order_id].created_at),
    )
    rest = [p for p in parcels if p.order_id not in open_retries]
    failed = sorted(
        (p for p in rest if p.status == "failed"),
        key=lambda p: p.failed_at or p.updated_at or datetime.min.replace(tzinfo=UTC),
    )
    late = sorted(
        (
            p
            for p in rest
            if p.status != "failed" and is_delayed(p.status, p.eta_max, today)
        ),
        key=lambda p: p.eta_max,
    )
    out = (
        [("retry_request", p) for p in asked]
        + [("failed", p) for p in failed]
        + [("delayed", p) for p in late]
    )
    return out[:QUEUE_MAX]


def delivery_days(p: Parcel) -> float | None:
    if p.delivered_at is None or p.shipped_at is None:
        return None
    return max(0.0, (p.delivered_at - p.shipped_at).total_seconds() / 86400)


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 2) if whole else None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def analytics(parcels: list[Parcel]) -> dict[str, Any]:
    """The 30-day figures (TRK-U-06).

    Success rate = delivered ÷ (delivered + returned). Failed-attempt rate =
    failed attempts ÷ (failed attempts + deliveries). Areas and carriers need
    ``MIN_PARCELS`` parcels to be ranked: areas worst first, carriers best.
    """
    delivered = [p for p in parcels if p.status == "delivered"]
    returned = sum(p.status == "returned" for p in parcels)
    failures = sum(p.attempts for p in parcels)

    def days(group: list[Parcel]) -> float | None:
        return _avg([d for p in group if (d := delivery_days(p)) is not None])

    def grouped(key: str) -> dict[Any, list[Parcel]]:
        out: dict[Any, list[Parcel]] = {}
        for p in parcels:
            value = getattr(p, key)
            if value:
                out.setdefault(value, []).append(p)
        return {k: g for k, g in out.items() if len(g) >= MIN_PARCELS}

    areas = [
        {
            "area": area_out(code, None),
            "parcels": len(g),
            "delivered_rate": _rate(sum(p.status == "delivered" for p in g), len(g)),
            "returned_rate": _rate(sum(p.status == "returned" for p in g), len(g)),
            "avg_days": days(g),
        }
        for code, g in grouped("governorate_code").items()
    ]
    carriers = [
        {
            "carrier": carrier_out(key),
            "parcels": len(g),
            "delivered_rate": _rate(sum(p.status == "delivered" for p in g), len(g)),
            "avg_days": days(g),
        }
        for key, g in grouped("carrier").items()
    ]
    return {
        "days": WINDOW.days,
        "min_parcels": MIN_PARCELS,
        "avg_delivery_days": days(delivered),
        "success_rate": _rate(len(delivered), len(delivered) + returned),
        "failed_attempt_rate": _rate(failures, failures + len(delivered)),
        "areas": sorted(areas, key=lambda a: (a["delivered_rate"], -a["parcels"])),
        "carriers": sorted(
            carriers, key=lambda c: (-(c["delivered_rate"] or 0), -c["parcels"])
        ),
    }


def merged_settings(row: Any | None) -> dict[str, Any]:
    if row is None:
        return dict(DEFAULT_SETTINGS)
    return {key: getattr(row, key) for key in DEFAULT_SETTINGS}
