"""Single source of truth for every tracking-credential validation rule.

Why this module exists
----------------------
Meta / TikTok pixel-ID, token and test-event-code rules used to be retyped
as literals in three repos (this schema module, ``numo-merchant-hub``, and
``numu-storefront``). They drifted, and the drift was merchant-visible: a
real 17-digit Meta Pixel ID was rejected by a ``^\\d{15,16}$`` whitelist in
the hub AND the API while the storefront happily rendered it.

The rule of thumb this module encodes:

    **If a constant describes something a third party owns, we do not
    enforce it — we bound it loosely and surface the provider's error.**

So the regexes here are deliberately permissive. They exist to catch the
paste errors we actually see (``act_123…``, a whole URL, letters in a
numeric field, empty strings) and nothing more. Deciding whether a pixel
ID is *real* is Meta's job, answered by the ``POST …/tracking/meta/verify``
endpoint, not by counting digits.

Consumers
---------
* ``schemas/tenant/tracking.py`` — Pydantic field validators.
* ``GET /api/v1/stores/{id}/settings/tracking/validation-contract`` —
  serves this module verbatim so the hub can drive its client-side
  validation from the API. That means the API can loosen a rule without a
  frontend deploy, and structural drift becomes impossible.

Review triggers for the values below live in ``docs/external-contracts.md``.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Meta (Facebook / Instagram)
# ---------------------------------------------------------------------------

# Numeric, up to 20 digits. NOT a length whitelist: Meta publishes no
# normative length for Pixel / Dataset IDs, and allocates them from a
# 64-bit space (unsigned max = 18446744073709551615 → 20 digits) that grows
# monotonically. The old "15-16 digits" bound was community folklore
# describing the IDs that happened to exist when those blog posts were
# written; 2026-minted datasets are already past 16. 20 is the arithmetic
# ceiling, so this can never reject a valid ID.
META_PIXEL_ID_RE: Final[str] = r"^\d{6,20}$"

# Meta's Events Manager usually generates ``TEST12345``, but the field is a
# free-form string and merchants paste codes from other tools. Matching
# TikTok's rule rather than asserting Meta's generator format.
META_TEST_EVENT_CODE_RE: Final[str] = r"^[A-Za-z0-9_-]{1,64}$"

# System-User tokens are ~200 chars in practice, but the length is Meta's to
# change and a too-short *real* token blocked client-side with no server
# reason is a worse failure than one round-trip that returns Meta's own
# error. 20 is a sanity floor only — the hub used to enforce 50, i.e. it was
# STRICTER than the API it posts to.
META_MIN_TOKEN_LENGTH: Final[int] = 20

# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

# TikTok's "Pixel Code" is alphanumeric, ~20 chars (e.g. C4A2B1D3E4F5G6H7I8J9).
# Deliberately not digits-only. This rule was already consistent across the
# three repos — it is here for governance, not because it was broken.
TIKTOK_PIXEL_ID_RE: Final[str] = r"^[A-Za-z0-9]{6,40}$"
TIKTOK_TEST_EVENT_CODE_RE: Final[str] = r"^[A-Za-z0-9_-]{1,64}$"
TIKTOK_MIN_TOKEN_LENGTH: Final[int] = 10


# ---------------------------------------------------------------------------
# Compiled forms + helpers
# ---------------------------------------------------------------------------

_META_PIXEL_ID = re.compile(META_PIXEL_ID_RE)
_META_TEST_EVENT_CODE = re.compile(META_TEST_EVENT_CODE_RE)
_TIKTOK_PIXEL_ID = re.compile(TIKTOK_PIXEL_ID_RE)
_TIKTOK_TEST_EVENT_CODE = re.compile(TIKTOK_TEST_EVENT_CODE_RE)


def _matches(pattern: re.Pattern[str], value: str) -> bool:
    """``fullmatch``, not ``match``.

    Python's ``$`` also matches immediately *before* a trailing newline, so
    ``re.match(r"^\\d{6,20}$", "1712515290084839\\n")`` succeeds — a pixel ID
    pasted with a trailing newline would validate and then be interpolated
    straight into a Graph API path. ``fullmatch`` has to consume the whole
    string, so the newline fails as it should. ECMAScript's ``$`` is already
    strict this way, which is why the pattern strings stay JS-portable.
    Callers strip surrounding whitespace before getting here.
    """
    return bool(pattern.fullmatch(value))


def is_valid_meta_pixel_id(value: str) -> bool:
    return _matches(_META_PIXEL_ID, value)


def is_valid_meta_test_event_code(value: str) -> bool:
    return _matches(_META_TEST_EVENT_CODE, value)


def is_valid_tiktok_pixel_id(value: str) -> bool:
    return _matches(_TIKTOK_PIXEL_ID, value)


def is_valid_tiktok_test_event_code(value: str) -> bool:
    return _matches(_TIKTOK_TEST_EVENT_CODE, value)


# Human-readable failure copy. Kept next to the rules so the message and the
# rule can never describe different things — the old error text
# ("must be 15-16 digits") outlived the rule it documented in two repos.
META_PIXEL_ID_ERROR: Final[str] = (
    "pixel_id must be numeric, up to 20 digits (Meta Pixel / Dataset ID)"
)
META_TEST_EVENT_CODE_ERROR: Final[str] = (
    "test_event_code must be alphanumeric (dash/underscore ok), e.g. TEST12345"
)
TIKTOK_PIXEL_ID_ERROR: Final[str] = (
    "pixel_id must be 6-40 alphanumeric chars (TikTok Pixel Code)"
)
TIKTOK_TEST_EVENT_CODE_ERROR: Final[str] = (
    "test_event_code must be alphanumeric (dash/underscore ok)"
)


def validation_contract() -> dict:
    """The machine-readable contract served to the merchant hub.

    Shape is intentionally flat and JS-regex-safe: every pattern here uses
    only syntax that is identical in Python's ``re`` and ECMAScript, so the
    hub can hand the string straight to ``new RegExp(...)``.
    """
    return {
        "meta": {
            "pixel_id": META_PIXEL_ID_RE,
            "pixel_id_error": META_PIXEL_ID_ERROR,
            "test_event_code": META_TEST_EVENT_CODE_RE,
            "test_event_code_error": META_TEST_EVENT_CODE_ERROR,
            "min_token_length": META_MIN_TOKEN_LENGTH,
        },
        "tiktok": {
            "pixel_id": TIKTOK_PIXEL_ID_RE,
            "pixel_id_error": TIKTOK_PIXEL_ID_ERROR,
            "test_event_code": TIKTOK_TEST_EVENT_CODE_RE,
            "test_event_code_error": TIKTOK_TEST_EVENT_CODE_ERROR,
            "min_token_length": TIKTOK_MIN_TOKEN_LENGTH,
        },
    }


__all__ = [
    "META_MIN_TOKEN_LENGTH",
    "META_PIXEL_ID_ERROR",
    "META_PIXEL_ID_RE",
    "META_TEST_EVENT_CODE_ERROR",
    "META_TEST_EVENT_CODE_RE",
    "TIKTOK_MIN_TOKEN_LENGTH",
    "TIKTOK_PIXEL_ID_ERROR",
    "TIKTOK_PIXEL_ID_RE",
    "TIKTOK_TEST_EVENT_CODE_ERROR",
    "TIKTOK_TEST_EVENT_CODE_RE",
    "is_valid_meta_pixel_id",
    "is_valid_meta_test_event_code",
    "is_valid_tiktok_pixel_id",
    "is_valid_tiktok_test_event_code",
    "validation_contract",
]
