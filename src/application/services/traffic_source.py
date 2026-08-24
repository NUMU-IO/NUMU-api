"""Derive a traffic source when the landing URL didn't declare one.

Only merchant-tagged links carry an explicit ``utm_source``. Everything
else arrives bare, and reading ``utm_source`` alone files all of it under
"Direct". This module is the single fallback chain, shared by the funnel
rows, the journey touches, the order and its abandoned-checkout twin, so
all four name the same visit the same way.

Chain — first rung that resolves wins:

  1. An explicit ``utm_source``. Merchant intent always beats inference.
  2. An ad-platform click id (``ttclid`` / ``fbclid`` / ``gclid``).
     Medium ``paid`` — a click id only exists on a paid ad click.
  3. The in-app browser's User-Agent. Medium ``social``.
  4. The referrer host. Medium ``organic`` / ``social`` / ``referral``.

Why the User-Agent outranks the referrer, which is the opposite of the
usual analytics convention: an in-app browser is unambiguous proof of the
app the visitor came from, whereas a referrer is often an intermediary. A
TikTok bio link routed through linktr.ee reports ``linktr.ee`` — true, but
useless to a merchant asking whether TikTok worked.

Both rungs are needed because the platforms differ in what they send.
Measured on production over 30 days:

  * Instagram sends a referrer (``l.instagram.com``, 166 visits) but its
    organic traffic carries no click id, so rung 4 is what recovers it.
  * TikTok sends NO referrer from its webview — 268 page views carried a
    TikTok User-Agent while the only ``tiktok.com`` referrer ever recorded
    was ``ads.tiktok.com`` (2 hits, the merchant's own ads manager). Rung
    3 is the only thing that can see them.

Keep this table in step with the storefront's ``cart-track-attribution.ts``
or an order and its abandoned-checkout twin would name one visit twice.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Checked in this order when a single touch carries more than one click id
# — practically never, since a touch is a snapshot of ONE landing URL and
# each platform appends only its own id.
CLICK_ID_SOURCES: tuple[tuple[str, str], ...] = (
    ("ttclid", "tiktok"),
    ("fbclid", "facebook"),
    ("gclid", "google"),
)

MEDIUM_PAID = "paid"
MEDIUM_SOCIAL = "social"
MEDIUM_ORGANIC = "organic"
MEDIUM_REFERRAL = "referral"
MEDIUM_EMAIL = "email"

# Medium stamped on click-id-derived sources. Retained under the original
# name because callers and tests import it.
DERIVED_MEDIUM = MEDIUM_PAID

# In-app browser User-Agent markers, lowercased. Order is priority: a
# webview can carry more than one vendor token (Instagram's embeds Meta's
# `FBAV`), so the more specific app is listed first.
IN_APP_BROWSER_SOURCES: tuple[tuple[str, str], ...] = (
    # TikTok ships its webview under several build names — `musical_ly`
    # and `aweme` are the app's original and CN bundle ids and still
    # appear in the wild alongside `BytedanceWebview`.
    ("bytedancewebview", "tiktok"),
    ("bytelocwebview", "tiktok"),
    ("musical_ly", "tiktok"),
    ("aweme", "tiktok"),
    ("tiktok", "tiktok"),
    # Instagram before the Meta tokens: its webview reports BOTH.
    ("instagram", "instagram"),
    ("fb_iab", "facebook"),
    ("fban", "facebook"),
    ("fbav", "facebook"),
    ("snapchat", "snapchat"),
    ("pinterest", "pinterest"),
    ("linkedinapp", "linkedin"),
)

# Referrer host suffix -> (source, medium). Matched against the parsed
# host, so `l.instagram.com` resolves through the `instagram.com` entry.
# An allowlist by construction: a host we don't recognise — including the
# store's own domain on internal navigation — derives nothing.
REFERRER_SOURCES: tuple[tuple[str, str, str], ...] = (
    # Exact android-app netloc, before the Google rule below would claim
    # it: mail is not search traffic.
    ("com.google.android.gm", "gmail", MEDIUM_EMAIL),
    ("tiktok.com", "tiktok", MEDIUM_SOCIAL),
    ("instagram.com", "instagram", MEDIUM_SOCIAL),
    ("facebook.com", "facebook", MEDIUM_SOCIAL),
    ("messenger.com", "messenger", MEDIUM_SOCIAL),
    ("whatsapp.com", "whatsapp", MEDIUM_SOCIAL),
    ("snapchat.com", "snapchat", MEDIUM_SOCIAL),
    ("pinterest.com", "pinterest", MEDIUM_SOCIAL),
    ("linkedin.com", "linkedin", MEDIUM_SOCIAL),
    ("youtube.com", "youtube", MEDIUM_SOCIAL),
    ("twitter.com", "twitter", MEDIUM_SOCIAL),
    ("x.com", "twitter", MEDIUM_SOCIAL),
    ("t.co", "twitter", MEDIUM_SOCIAL),
    ("bing.com", "bing", MEDIUM_ORGANIC),
    ("yahoo.com", "yahoo", MEDIUM_ORGANIC),
    ("duckduckgo.com", "duckduckgo", MEDIUM_ORGANIC),
    ("linktr.ee", "linktree", MEDIUM_REFERRAL),
    ("beacons.ai", "beacons", MEDIUM_REFERRAL),
    ("chatgpt.com", "chatgpt", MEDIUM_REFERRAL),
    ("perplexity.ai", "perplexity", MEDIUM_REFERRAL),
)

# Google's ccTLDs are unbounded (google.com, google.com.eg, google.co.uk),
# so they get a pattern rather than table rows.
_GOOGLE_HOST = re.compile(r"(?:^|\.)google\.[a-z]{2,}(?:\.[a-z]{2,})?$")


def _read(touch: Any, attr: str) -> Any:
    if isinstance(touch, dict):
        return touch.get(attr)
    return getattr(touch, attr, None)


def derive_source_from_click_ids(touch: Any) -> str | None:
    """Canonical platform slug for the click id on ``touch``, or ``None``.

    ``touch`` may be an ``AttributionTouch``, an ORM row, a dict, or None.
    """
    if touch is None:
        return None
    for attr, source in CLICK_ID_SOURCES:
        value = _read(touch, attr)
        if isinstance(value, str) and value.strip():
            return source
    return None


def derive_source_from_user_agent(
    user_agent: str | None,
) -> tuple[str, str] | None:
    """``(source, medium)`` for a recognised in-app browser, else ``None``."""
    if not user_agent:
        return None
    ua = user_agent.lower()
    for marker, source in IN_APP_BROWSER_SOURCES:
        if marker in ua:
            return source, MEDIUM_SOCIAL
    return None


def derive_source_from_referrer(referrer: str | None) -> tuple[str, str] | None:
    """``(source, medium)`` for a recognised referrer host, else ``None``."""
    if not referrer:
        return None
    raw = referrer.strip()
    if not raw:
        return None
    # A bare host with no scheme parses as a path, not a netloc.
    if "//" not in raw:
        raw = f"//{raw}"
    try:
        host = (urlparse(raw).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None

    for suffix, source, medium in REFERRER_SOURCES:
        if host == suffix or host.endswith(f".{suffix}"):
            return source, medium
    if _GOOGLE_HOST.search(host):
        return "google", MEDIUM_ORGANIC
    return None


def effective_utm_source_medium(
    touch: Any,
    utm_source: str | None,
    utm_medium: str | None,
    *,
    referrer: str | None = None,
    user_agent: str | None = None,
) -> tuple[str | None, str | None]:
    """``(utm_source, utm_medium)`` resolved through the fallback chain.

    An explicit ``utm_source`` always wins and is returned untouched. When
    it is absent each rung is tried in turn; the first that resolves fills
    the source, and supplies the medium only when the caller had none.

    ``referrer`` defaults to the one recorded on ``touch`` — pass the
    request's referrer explicitly when the visit produced no touch at all,
    which is exactly the untagged-organic case this chain exists for.
    """
    if utm_source:
        return utm_source, utm_medium

    derived = derive_source_from_click_ids(touch)
    if derived is not None:
        return derived, utm_medium or MEDIUM_PAID

    from_ua = derive_source_from_user_agent(user_agent)
    if from_ua is not None:
        return from_ua[0], utm_medium or from_ua[1]

    candidate_referrer = referrer or _read(touch, "referrer")
    from_referrer = derive_source_from_referrer(candidate_referrer)
    if from_referrer is not None:
        return from_referrer[0], utm_medium or from_referrer[1]

    return utm_source, utm_medium
