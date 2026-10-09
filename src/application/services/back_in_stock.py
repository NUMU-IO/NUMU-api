"""Back in Stock (``back-in-stock``): the app's rules, with no I/O.

docs/Plans/APPS/01-back-in-stock. Everything the routes, the event handler and
the tasks decide goes through here, so each rule has one definition and one
test: contacts, masking, the buyable rule, caps, pacing, links, theme
readiness, attribution and retention.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

SLUG = "back-in-stock"
PLATFORM_DOMAIN = "numueg.app"
#: The WhatsApp system template (core/whatsapp_rich_templates.py).
TEMPLATE_NAME = "app_back_in_stock_v1"

CONTACT_CHOICES = ("phone_or_email", "phone", "email")
#: BIS-D5. Without a settings row these apply.
DEFAULT_SETTINGS = {
    "contact": "phone_or_email",
    "signup_cap": 300,
    "wa_cap": 200,
    "email_cap": 1000,
}
CAP_LIMITS = {"signup_cap": 5000, "wa_cap": 5000, "email_cap": 20000}
#: One contact waits for at most this many variants in one store.
MAX_WAITING_PER_CONTACT = 10

#: BIS-D7: the event handler's check runs this long after a stock change.
CHECK_DELAY_SECONDS = 60
#: Send pacing: the first send waits FIRST_SEND_SECONDS, then one every
#: SEND_GAP_SECONDS, so a store never sends more than 10 a minute.
FIRST_SEND_SECONDS = 30
SEND_GAP_SECONDS = 6

ATTRIBUTION_WINDOW = timedelta(days=7)
#: BIS-D6.
WAITING_TTL = timedelta(days=180)
ERASE_AFTER = timedelta(days=30)

WAITING, QUEUED, NOTIFIED, FAILED = "waiting", "queued", "notified", "failed"
UNSUBSCRIBED, CLOSED = "unsubscribed", "closed"
STATUSES = (WAITING, QUEUED, NOTIFIED, FAILED, UNSUBSCRIBED, CLOSED)

#: Themes whose product page has the app slot, by marketplace slug: the first
#: version that has it. Filled by Phase G (the slot in all 19 themes); until
#: then every theme reports "needs an update".
SLOT_READY_THEME_VERSIONS: dict[str, str] = {}


# ─── Contacts ──────────────────────────────────────────────────────────

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "0123456789" * 2)
_EGYPT_MOBILE = re.compile(r"01[0125]\d{8}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def normalize_phone(raw: str | None) -> str | None:
    """An Egyptian mobile as ``+201…`` (E.164), or None.

    Accepts ``01x…``, ``+20``/``0020``/``20`` prefixes, a dropped leading 0,
    spaces and dashes, and Arabic-Indic digits. Landlines and foreign numbers
    are refused.
    """
    s = re.sub(r"[\s\-().]", "", (raw or "").translate(_DIGITS))
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("00"):
        s = s[2:]
    if s.startswith("20") and len(s) == 12:
        s = "0" + s[2:]
    elif len(s) == 10 and s.startswith("1"):
        s = "0" + s
    return f"+20{s[1:]}" if _EGYPT_MOBILE.fullmatch(s) else None


def normalize_email(raw: str | None) -> str | None:
    s = (raw or "").strip().lower()
    return s if len(s) <= 254 and _EMAIL.fullmatch(s) else None


def mask(contact: str | None) -> str:
    """``+20 10•• ••• 4567`` / ``m•••@gmail.com``; ``—`` once erased."""
    if not contact:
        return "—"
    if "@" in contact:
        local, _, domain = contact.partition("@")
        return f"{local[:1]}•••@{domain}"
    return f"{contact[:3]} {contact[3:5]}•• ••• {contact[-4:]}"


# ─── Stock ─────────────────────────────────────────────────────────────

SELLABLE_STATUSES = ("active", "unlisted")


def _status(value: Any) -> str:
    return str(getattr(value, "value", value))


def variant_buyable(variant: Any) -> bool:
    """In stock, or stock not tracked. Selling past zero is not "back"."""
    return not variant.track_inventory or variant.inventory_quantity > 0


def is_buyable(product: Any, variants: list[Any], variant_id: Any | None) -> bool:
    """Can a shopper buy this variant (or, with no variant, any variant) now."""
    if _status(product.status) not in SELLABLE_STATUSES:
        return False
    chosen = [v for v in variants if variant_id is None or v.id == variant_id]
    return any(variant_buyable(v) for v in chosen)


def restock_cap(units: int | None) -> int:
    """How many waiters one restock of a variant alerts: max(10 × units, 20).
    ``units`` None = stock not tracked."""
    return max(10 * (units or 0), 20)


def alert_cap(units: int | None, *, room_today: int) -> int:
    """The restock cap, never past what the day's cap still allows."""
    return max(0, min(restock_cap(units), room_today))


def send_countdowns(count: int) -> list[int]:
    """Seconds from now for each of ``count`` sends of one store."""
    return [FIRST_SEND_SECONDS + i * SEND_GAP_SECONDS for i in range(count)]


# ─── Links ─────────────────────────────────────────────────────────────


def new_token() -> str:
    """22 characters of base64url: 128 random bits."""
    return secrets.token_urlsafe(16)


def store_host(store: Any) -> str:
    """The store's own host: its custom domain, else ``<sub>.numueg.app``."""
    if store.custom_domain:
        return store.custom_domain.strip().rstrip("/")
    return f"{store.subdomain}.{PLATFORM_DOMAIN}"


def product_url(store: Any, slug: str, variant_id: Any | None, channel: str) -> str:
    query = {"variant": str(variant_id)} if variant_id else {}
    query |= {"utm_source": "numu_back_in_stock", "utm_medium": channel}
    return f"https://{store_host(store)}/products/{slug}?{urlencode(query)}"


def unsubscribe_url(store: Any, token: str) -> str:
    return f"https://{store_host(store)}/unsubscribe/{SLUG}/{token}"


def button_values(subdomain: str, link_token: str, unsub_token: str) -> tuple[str, str]:
    """The WhatsApp URL-button suffixes after ``https://numueg.app/a/``."""
    return f"{SLUG}/{subdomain}/{link_token}", f"{SLUG}/{subdomain}/u/{unsub_token}"


# ─── Theme readiness ───────────────────────────────────────────────────


def _version(v: str) -> tuple[int, ...]:
    core = v.split("+", 1)[0].split("-", 1)[0]
    return tuple(int(p) for p in core.split(".") if p.isdigit())


def theme_ready(theme_slug: str | None, version: str | None) -> bool:
    """Does the store's pinned theme version show the product-page slot."""
    first = SLOT_READY_THEME_VERSIONS.get(theme_slug or "")
    return bool(first and version) and _version(version) >= _version(first)


# ─── Attribution and retention ─────────────────────────────────────────


@dataclass(frozen=True)
class OrderLine:
    product_id: Any
    variant_id: Any | None
    total: int  # minor units


def attributed_revenue(
    *,
    notified_at: datetime,
    ordered_at: datetime,
    product_id: Any,
    variant_id: Any | None,
    lines: list[OrderLine],
) -> int | None:
    """The revenue an alert earned from this order, or None.

    The order came within 7 days of the alert and has a line with the
    product, and the variant when the shopper waited for one."""
    if not notified_at <= ordered_at <= notified_at + ATTRIBUTION_WINDOW:
        return None
    matched = [
        line
        for line in lines
        if line.product_id == product_id
        and (variant_id is None or line.variant_id == variant_id)
    ]
    return sum(line.total for line in matched) if matched else None


def retention_actions(
    *,
    status: str,
    created_at: datetime,
    updated_at: datetime,
    has_contact: bool,
    now: datetime,
) -> tuple[str | None, bool]:
    """BIS-D6 for one row: ``(new_status or None, erase_contact)``.

    Waiting rows close after 180 days. A contact is erased 30 days after the
    row was notified, failed, unsubscribed or closed."""
    if status == WAITING and created_at <= now - WAITING_TTL:
        return CLOSED, False
    done = status in (NOTIFIED, FAILED, UNSUBSCRIBED, CLOSED)
    return None, done and has_contact and updated_at <= now - ERASE_AFTER


def merged_settings(row: Any | None) -> dict[str, Any]:
    """The store's settings, with the defaults where it saved none."""
    if row is None:
        return dict(DEFAULT_SETTINGS)
    return {key: getattr(row, key) for key in DEFAULT_SETTINGS}


# ─── Messages ──────────────────────────────────────────────────────────


def product_label(title: str, variant_title: str | None) -> str:
    """``تيشيرت قطن — أسود / L``: what the shopper waited for."""
    return f"{title} — {variant_title}" if variant_title else title


def template_params(store_name: str, subdomain: str, waiter: Any) -> dict[str, str]:
    """Parameters of ``app_back_in_stock_v1``: body {{1}} product, {{2}} store;
    the two URL buttons' suffixes after ``https://numueg.app/a/``."""
    order_link, stop_link = button_values(
        subdomain, waiter.link_token, waiter.unsub_token
    )
    return {
        "product": product_label(waiter.product_title, waiter.variant_title),
        "store_name": store_name,
        "order_link": order_link,
        "stop_link": stop_link,
    }


_AR_DIGITS = str.maketrans("0123456789,.", "٠١٢٣٤٥٦٧٨٩٬٫")


def price_text(cents: int, currency: str, locale: str) -> str:
    """``EGP 1,250`` / ``١٬٢٥٠ ج.م``; piastres only when there are any."""
    amount = cents / 100
    number = f"{amount:,.0f}" if cents % 100 == 0 else f"{amount:,.2f}"
    if locale == "ar":
        unit = "ج.م" if currency == "EGP" else currency
        return f"{number.translate(_AR_DIGITS)} {unit}"
    return f"{currency} {number}"


_EMAIL_COPY = {
    "ar": {
        "subject": "{product} رجع تاني في {store}",
        "preheader": "اللي كنت مستنيه رجع — اطلبه قبل ما يخلص.",
        "button": "اطلبه دلوقتي",
        "footer": "بعتنالك الإيميل ده عشان طلبت تنبيه من {store}.",
        "stop": "وقّف التنبيهات",
    },
    "en": {
        "subject": "{product} is back at {store}",
        "preheader": "The item you wanted is back.",
        "button": "Order now",
        "footer": "You're getting this email because you asked {store} for an alert.",
        "stop": "Stop alerts",
    },
}


def email_message(
    *,
    store: Any,
    waiter: Any,
    product_slug: str,
    image: str | None,
    price: str | None,
) -> dict[str, Any]:
    """Subject, HTML, text and the one-click unsubscribe headers of one alert."""
    from html import escape

    lang = "en" if waiter.locale == "en" else "ar"
    copy = _EMAIL_COPY[lang]
    label = product_label(waiter.product_title, waiter.variant_title)
    order = product_url(store, product_slug, waiter.variant_id, "email")
    stop = unsubscribe_url(store, waiter.unsub_token)
    direction = "rtl" if lang == "ar" else "ltr"
    logo = (
        f'<img src="{escape(store.logo_url)}" alt="{escape(store.name)}" height="40"><br>'
        if getattr(store, "logo_url", None)
        else f"<strong>{escape(store.name)}</strong><br>"
    )
    picture = f'<img src="{escape(image)}" alt="" width="280"><br>' if image else ""
    price_html = f'<p><bdi dir="ltr">{escape(price)}</bdi></p>' if price else ""
    html = (
        f'<div dir="{direction}" style="font-family:Arial,sans-serif;max-width:480px;margin:auto">'
        f'<span style="display:none">{escape(copy["preheader"])}</span>'
        f"{logo}{picture}<h2>{escape(label)}</h2>{price_html}"
        f'<p><a href="{escape(order)}" style="background:#111;color:#fff;padding:12px 20px;'
        f'border-radius:8px;text-decoration:none">{escape(copy["button"])}</a></p>'
        f'<p style="color:#666;font-size:12px">{escape(copy["footer"].format(store=store.name))}'
        f' · <a href="{escape(stop)}">{escape(copy["stop"])}</a></p></div>'
    )
    text = f"{label}\n{order}\n\n{copy['footer'].format(store=store.name)}\n{copy['stop']}: {stop}"
    return {
        "subject": copy["subject"].format(product=label, store=store.name),
        "html": html,
        "text": text,
        "headers": {
            "List-Unsubscribe": f"<{stop}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    }
