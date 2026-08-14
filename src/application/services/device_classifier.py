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

import re
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


# ── Bot / internal-traffic detection (analytics hygiene) ───────────────────
#
# Found on a real store (vionne, 2026-08-14): "2,487 visitors" against ~350
# human sessions. Crawlers keep no cookies, so EVERY crawled page mints a
# fresh session fingerprint — one bot walking the catalog registers as
# hundreds of unique visitors, and the merchant's own theme-editor previews
# counted too. Conversion rate looked like 0.28% on a store converting ~2%
# of actual humans. These checks run at INGEST so junk never lands in
# page_views / funnel_events at all (cheap regex; no per-row query cost).

_BOT_UA_MARKERS = (
    "crawler",
    "crawl/",
    "spider",
    "slurp",
    "headless",
    "lighthouse",
    "pagespeed",
    "pingdom",
    "uptimerobot",
    "statuscake",
    "facebookexternalhit",
    "whatsapp",
    "telegrambot",
    "twitterbot",
    "skypeuripreview",
    "discordbot",
    "vkshare",
    "curl/",
    "wget/",
    "python-requests",
    "python-httpx",
    "aiohttp",
    "go-http-client",
    "okhttp",
    "scrapy",
    "phantomjs",
    "puppeteer",
    "playwright",
    "selenium",
    "gptbot",
    "oai-searchbot",
    "chatgpt-user",
    "claudebot",
    "claude-web",
    "perplexitybot",
    "bytespider",
    "petalbot",
    "semrush",
    "ahrefs",
    "mj12bot",
    "dotbot",
    "dataforseo",
)

# Referrer hosts that mean "this is us, not a shopper" — the merchant hub's
# theme editor / preview iframes and the admin backoffice.
_INTERNAL_REFERRER_MARKERS = (
    "merchant.numueg.app",
    "admin.numueg.app",
)


# "bot" needs a boundary check, not a bare substring: Cubot is a real
# Android phone brand ("CUBOT NOTE ..." UAs), while every actual bot UA
# has "bot" at a word END (Googlebot, bingbot, GPTBot, DuckDuckBot ...).
_BOT_WORD_RE = re.compile(r"bot(?:[\s/;)\],+]|$)")


def is_bot_user_agent(user_agent: str | None) -> bool:
    """True when the UA is a known bot/automation signature or absent.

    A MISSING UA is treated as bot: every real storefront browser sends
    one, while the cheapest scripted traffic doesn't. ua-parser's device
    family "Spider" is also honoured for signatures the marker list
    misses.
    """
    if not user_agent or not user_agent.strip():
        return True
    lowered = user_agent.lower()
    if _BOT_WORD_RE.search(lowered):
        return True
    if any(marker in lowered for marker in _BOT_UA_MARKERS):
        return True
    parsed = parse(user_agent)
    family = ((parsed.device.family if parsed.device else "") or "").lower()
    return family == "spider"


def is_internal_traffic(referrer: str | None, path: str | None = None) -> bool:
    """True for the platform's own surfaces browsing the storefront.

    The theme editor iframes the live store (referrer = merchant hub) and
    preview navigations carry the ``_npt`` preview-token param — neither
    is a shopper, and both were inflating visitor counts.
    """
    if referrer and any(m in referrer.lower() for m in _INTERNAL_REFERRER_MARKERS):
        return True
    return bool(path and "_npt=" in path)
