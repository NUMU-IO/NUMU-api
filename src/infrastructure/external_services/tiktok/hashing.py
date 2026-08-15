"""PII hashing helpers for the TikTok Events API.

TikTok's Events API v1.3 wants the same lowercase-trimmed SHA-256 digests
that Meta requires for email / phone / name / location fields; ``ttclid``,
``ttp``, IP and user-agent are passed verbatim.

To keep a single source of truth for the *hard* parts — MENA phone
normalization and Egyptian-Arabic name transliteration — this module
**reuses the canonical primitives from ``meta/hashing.py``** rather than
re-implementing them (which would silently drift on case/trim/phone-format
and destroy browser↔server dedup). Only the OUTPUT SHAPE differs: TikTok's
``user`` object keys are ``email`` / ``phone`` / ``external_id`` / … , not
Meta's ``em`` / ``ph`` / ``external_id``.

See ``TikTok Events API`` docs: the ``user`` object accepts hashed
identifiers and raw click/session/context signals.
"""

from __future__ import annotations

from typing import Any

# Deliberate cross-module reuse of the canonical, battle-tested hashing
# primitives. These are the risky bits (Arabic transliteration + MENA phone
# canonicalization); re-implementing them here would be a footgun. If a
# future refactor extracts them into ``external_services/_shared``, update
# this import — the public ``hash_tiktok_user_data`` contract stays stable.
from src.infrastructure.external_services.meta.hashing import (
    _country_hash,
    _h,
    _h_each,
    _normalize_mena_phone,
    _normalize_name,
    _zip_hash,
)


def _first(values: list[str] | None) -> str | None:
    """Collapse a multi-variant hash list to the primary (first) digest.

    Meta accepts multi-value AM fields (``[latin_hash, arabic_hash]``);
    TikTok's user object expects a single hashed string per identifier, so
    we take the first (Latin/primary) variant. Arabic-only names still hash
    via their transliterated Latin form (``_normalize_name`` puts the Latin
    variant first).
    """
    if not values:
        return None
    return values[0]


def _first_external_id(raw: dict) -> str | None:
    """Hashed ``external_id`` — customer id when known, else session id.

    Mirrors Meta's ``_external_ids`` but collapsed to one value, because
    TikTok's Events API user object takes a single hashed string here.
    """
    for key in ("customer_id", "external_id"):
        value = raw.get(key)
        if value:
            return _h(str(value))
    return None


def hash_tiktok_user_data(raw: dict) -> dict[str, Any]:
    """Convert a raw user-data dict into TikTok's Events API ``user`` shape.

    Input keys are NUMU's internal vocabulary (``email``, ``phone``,
    ``first_name``, ``city`` …); output keys match TikTok's Events API
    ``user`` object. Email / phone / names / location are SHA-256 hashed;
    ``ttclid`` / ``ttp`` / ``ip`` / ``user_agent`` are raw per spec.

    Empty fields are dropped entirely (returned dict omits ``None`` values)
    so the outbound payload — and the redacted ``tiktok_event_log`` copy —
    stays minimal.
    """
    user: dict[str, Any] = {
        # Hashed identifiers.
        "email": _h(raw["email"]) if raw.get("email") else None,
        "phone": (
            _h(_normalize_mena_phone(raw["phone"])) if raw.get("phone") else None
        ),
        # Customer id when authenticated, else the pseudonymous session
        # fingerprint. TikTok's `external_id` is a single string (unlike
        # Meta's array), so this is a preference order, not both. Reading
        # `customer_id` alone meant guest checkouts — most MENA orders —
        # sent no external_id on any mid-funnel event.
        "external_id": _first_external_id(raw),
        # Hashed location / name fields (TikTok supports these on the user
        # object for match-quality lift). Reuse Meta's Arabic-aware
        # normalizer, then collapse to the primary variant.
        "first_name": _first(
            _h_each(_normalize_name(raw.get("first_name"), field="fn"))
        ),
        "last_name": _first(_h_each(_normalize_name(raw.get("last_name"), field="ln"))),
        # `strip_spaces=True` keeps this byte-identical to Meta's `ct`. Both
        # vendors index the space-free lowercase form, and when only Meta's
        # side was corrected the two silently diverged — "New Cairo" hashing
        # as `newcairo` for Meta and `new cairo` for TikTok, with TikTok left
        # holding the form that matches nothing.
        "city": _first(
            _h_each(_normalize_name(raw.get("city"), field="ct", strip_spaces=True))
        ),
        # Same whitespace/dash rule as Meta's `_zip_hash` — a code entered as
        # "12345-678" must not hash differently from "12345678".
        "zip_code": _first(_zip_hash(raw.get("zip"))),
        # Canonicalize to lowercase ISO-3166-1 alpha-2 before hashing, and
        # DROP anything unmappable: a digest of "Egypt" matches nothing, and
        # a field that can never match is worse than an absent one.
        "country": _first(_country_hash(raw.get("country_code"))),
        # Raw context/click signals — NOT hashed.
        "ttclid": raw.get("ttclid"),
        "ttp": raw.get("ttp"),
        "ip": raw.get("ip"),
        "user_agent": raw.get("user_agent"),
    }
    # Drop empties so the payload (and its stored redacted copy) is minimal.
    return {k: v for k, v in user.items() if v}
