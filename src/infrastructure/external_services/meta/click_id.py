"""Meta click-id (``fbc``) construction — ONE implementation, every call site.

Meta's spec (Customer Information Parameters → "ClickID and the fbp and fbc
Parameters", verified 2026-08-17)::

    fbc = fb.{subdomainIndex}.{creationTime}.{fbclid}

with two rules that were both being got wrong before this module existed:

**creationTime is the CLICK time, not the event time.** Verbatim: *"creationTime
is the UNIX time since epoch in milliseconds when the ``_fbc`` was stored. If you
don't save the ``_fbc`` cookie, use the timestamp when you first observed or
received this ``fbclid`` value."* The previous implementation passed the event's
own timestamp, so a Purchase three days after the click claimed the click
happened at purchase time — which can push the join outside Meta's
click-attribution window and lose the conversion's ad credit outright. It also
meant every event in one session synthesized a *different* ``fbc`` for the same
click, so nothing joined to anything.

**subdomainIndex counts the labels of the domain the cookie is set on**
(``com`` = 0, ``example.com`` = 1, ``www.example.com`` = 2). The browser Pixel
writes the cookie on the registrable domain (eTLD+1), so the index we must
reproduce equals **the number of labels in the public suffix**:

    vionne.numueg.app  → suffix ``app``     (1 label) → index 1
    vionne.com.eg      → suffix ``com.eg``  (2 labels) → index 2

Hardcoding ``1`` was right for ``*.numueg.app`` and silently wrong the moment a
merchant brings a ``.com.eg`` custom domain — which is the normal shape of an
Egyptian business domain.

⚠️ ``fbclid`` is **case sensitive**: *"do not apply any modifications before
using"*. It is never lowercased, trimmed into, or otherwise touched here.
"""

from __future__ import annotations

from datetime import datetime

# Multi-label public suffixes NUMU merchants realistically use. Anything not
# listed is assumed to be a single-label TLD (.com, .app, .shop, .store, .eg…),
# which is the overwhelmingly common case and yields index 1.
#
# This is deliberately a short, market-scoped list rather than a vendored copy
# of the Public Suffix List: the PSL is ~10k entries that would need its own
# refresh cadence, and getting the index wrong only degrades one match key on
# custom domains we do not yet serve. Revisit when custom domains ship
# (META-SIGNAL-QUALITY-PLAN W8) — that is the point at which a real PSL
# dependency earns its keep.
_MULTI_LABEL_SUFFIXES: frozenset[str] = frozenset({
    # Egypt
    "com.eg",
    "org.eg",
    "net.eg",
    "edu.eg",
    "gov.eg",
    "sci.eg",
    # Gulf / MENA
    "com.sa",
    "net.sa",
    "org.sa",
    "edu.sa",
    "gov.sa",
    "com.ae",
    "net.ae",
    "org.ae",
    "ac.ae",
    "gov.ae",
    "com.kw",
    "com.qa",
    "com.bh",
    "com.om",
    "com.jo",
    "com.lb",
    "com.ly",
    "com.tn",
    "com.dz",
    "com.ma",
    "co.ma",
    # Common elsewhere — cheap to include, avoids a wrong index if a
    # merchant brings one.
    "co.uk",
    "org.uk",
    "me.uk",
    "com.au",
    "net.au",
    "org.au",
    "co.nz",
    "co.za",
    "com.tr",
    "com.br",
    "com.mx",
})


def subdomain_index_for_host(host: str | None) -> int:
    """Meta's ``subdomainIndex`` for the domain the Pixel sets its cookie on.

    Returns the number of labels in the host's public suffix, which is the
    index the browser Pixel would have written. Defaults to 1 — the correct
    value for every single-label TLD, and for ``*.numueg.app`` specifically.
    """
    if not host:
        return 1
    clean = host.strip().lower().split(":", 1)[0].strip(".")
    if not clean:
        return 1
    labels = clean.split(".")
    if len(labels) >= 2 and ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return 2
    return 1


def synthesize_fbc(
    fbclid: str | None,
    click_ts: datetime | int | float | None,
    *,
    host: str | None = None,
) -> str | None:
    """Rebuild Meta's ``fbc`` from a raw ``fbclid`` we captured ourselves.

    The browser Pixel writes ``_fbc`` itself — but only if it loaded. Ad
    blockers and DNS filtering are common in Egypt, and the cookie is also
    absent whenever the visitor's first landing predates the Pixel being
    configured. Meta explicitly supports reconstructing the value server-side
    in exactly those cases.

    ``click_ts`` must be **when the fbclid was first observed** (the landing),
    not when the event fired. Accepts a datetime or an epoch value in seconds
    or milliseconds. Returns None when there is no click id or no usable
    timestamp — a fabricated ``fbc`` is worse than none, because it claims an
    ad click that Meta cannot join.
    """
    if not fbclid:
        return None

    ms = _to_epoch_ms(click_ts)
    if ms is None:
        return None

    return f"fb.{subdomain_index_for_host(host)}.{ms}.{fbclid}"


def _to_epoch_ms(value: datetime | int | float | None) -> int | None:
    """Coerce a datetime / epoch-seconds / epoch-millis value to epoch millis."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    # Heuristic: anything below ~1e11 is seconds (1e11 s ≈ year 5138), above
    # is already milliseconds. The attribution envelope has carried both.
    return int(num * 1000) if num < 1e11 else int(num)
