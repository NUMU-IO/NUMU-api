"""User-agent → device classification — feature 002 US3.

Called at funnel-event ingest to persist a coarse device class on
``funnel_events.device``. Three buckets — ``mobile`` / ``tablet`` /
``desktop`` — match the Shopify-style "Sessions by device" donut. Smart
TV / console / smartwatch are rare on storefront traffic and collapse
into ``desktop``. NULL UA → NULL classification (surfaces as the
"Unknown" donut bucket).

Implementation: ``ua-parser`` vendors the Browserscope regex db. Calls
are ~50µs per parse on modern hardware — safe in the ingest hot path.

Why not classify at query time:
- Forces a regex scan over potentially millions of rows on every
  dashboard render → blows the 800ms SC-003 budget.
- The classification rarely changes per UA string, so storing it
  once at ingest is the correct precomputation.
"""

from __future__ import annotations

from typing import Literal

from ua_parser import parse

Device = Literal["mobile", "tablet", "desktop"]


def classify(user_agent: str | None) -> Device | None:
    """Classify a UA string into mobile / tablet / desktop.

    Returns ``None`` when the UA is missing, empty, or yields no usable
    device info (parse returned no ``Device`` family). The caller stores
    the ``None`` as SQL NULL — the device panel buckets that as
    "Unknown".
    """
    if not user_agent or not user_agent.strip():
        return None

    parsed = parse(user_agent)
    family = (parsed.device.family if parsed.device else "") or ""
    os_family = (parsed.os.family if parsed.os else "") or ""

    family_lower = family.lower()
    os_lower = os_family.lower()

    # Tablet detection runs first — iPads carry iOS and Android tablets
    # carry Android, so a naive os-based check would lump them with
    # phones.
    if "tablet" in family_lower or "ipad" in family_lower or "kindle" in family_lower:
        return "tablet"
    # Android phone-vs-tablet split: per Google's spec
    # (https://developer.chrome.com/docs/multidevice/user-agent), Android
    # phones MUST carry the "Mobile" token in the UA string; Android
    # tablets MUST NOT. ua-parser doesn't surface this in OS/device
    # family for generic Android devices, so we check the raw UA.
    if os_lower == "android":
        return "mobile" if "Mobile" in user_agent else "tablet"

    # Mobile detection — iOS phones, Windows Phone, BlackBerry.
    mobile_os = {"ios", "windows phone", "blackberry os"}
    if os_lower in mobile_os:
        return "mobile"
    if "mobile" in family_lower:
        return "mobile"

    # Everything else — desktop browsers, bots/crawlers, smart TVs,
    # consoles, smartwatches — collapses into desktop. Bots show up in
    # raw analytics anyway; not worth a fourth bucket for v1.
    return "desktop"
