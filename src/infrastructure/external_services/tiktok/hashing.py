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
    _h,
    _h_each,
    _normalize_mena_phone,
    _normalize_name,
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
        "external_id": _h(raw["customer_id"]) if raw.get("customer_id") else None,
        # Hashed location / name fields (TikTok supports these on the user
        # object for match-quality lift). Reuse Meta's Arabic-aware
        # normalizer, then collapse to the primary variant.
        "first_name": _first(
            _h_each(_normalize_name(raw.get("first_name"), field="fn"))
        ),
        "last_name": _first(_h_each(_normalize_name(raw.get("last_name"), field="ln"))),
        "city": _first(_h_each(_normalize_name(raw.get("city"), field="ct"))),
        "zip_code": _h(raw["zip"]) if raw.get("zip") else None,
        "country": _h(raw["country_code"]) if raw.get("country_code") else None,
        # Raw context/click signals — NOT hashed.
        "ttclid": raw.get("ttclid"),
        "ttp": raw.get("ttp"),
        "ip": raw.get("ip"),
        "user_agent": raw.get("user_agent"),
    }
    # Drop empties so the payload (and its stored redacted copy) is minimal.
    return {k: v for k, v in user.items() if v}
