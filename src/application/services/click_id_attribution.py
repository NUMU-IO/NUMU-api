"""Derive a traffic source from ad-platform click ids.

Ad platforms rarely tag their clicks with UTMs — each appends only its own
click id to the landing URL (``ttclid`` for TikTok, ``fbclid`` for Meta,
``gclid`` for Google). Reading ``utm_source`` alone therefore files every
untagged ad visit under "Direct", and a visit whose envelope still carries
an OLDER click id under the wrong platform.

One rule, shared by the funnel rows, the journey touches and the order, so
all three name a visit the same way the storefront's abandoned-checkout
payload already does (``cart-track-attribution.ts`` — keep the two tables
in step).
"""

from __future__ import annotations

from typing import Any

# Checked in this order when a single touch carries more than one click id
# — practically never, since a touch is a snapshot of ONE landing URL and
# each platform appends only its own id.
CLICK_ID_SOURCES: tuple[tuple[str, str], ...] = (
    ("ttclid", "tiktok"),
    ("fbclid", "facebook"),
    ("gclid", "google"),
)

# Medium stamped on click-id-derived sources so they stay distinguishable
# from merchant-tagged links in channel reports.
DERIVED_MEDIUM = "paid"


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


def effective_utm_source_medium(
    touch: Any,
    utm_source: str | None,
    utm_medium: str | None,
) -> tuple[str | None, str | None]:
    """``(utm_source, utm_medium)`` with click-id fallback.

    An explicit ``utm_source`` always wins. When it is absent and the touch
    carries a click id, the platform slug fills ``utm_source`` and
    ``utm_medium`` defaults to ``"paid"`` unless one was supplied.
    """
    if utm_source:
        return utm_source, utm_medium
    derived = derive_source_from_click_ids(touch)
    if derived is None:
        return utm_source, utm_medium
    return derived, utm_medium or DERIVED_MEDIUM
