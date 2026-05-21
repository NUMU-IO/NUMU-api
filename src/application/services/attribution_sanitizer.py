"""Sanitize UTM and attribution strings on ingest.

Every UTM value the storefront sends is user-controlled (anyone can
craft a URL with arbitrary ``?utm_*`` params). We don't reject the
request — UTMs are non-sensitive data and many legitimate values
contain non-alphanumeric chars (Mailchimp campaign IDs use ``+``,
percent-encoded values, etc.). We strip what's actively harmful:

* Control characters (``\\x00–\\x1F`` and ``\\x7F``) — break CSV exports
  and log readers.
* ``<``, ``>``, ``"`` — not valid in any legitimate UTM and indicate
  tampering or a bad copy-paste. React auto-escapes these at render
  time, but defense-in-depth.

Length is capped at 200 chars to match the DB column size. Anything
longer is truncated (not rejected) — a too-long UTM still gives the
merchant useful attribution data without 4xx-ing the order.
"""

from __future__ import annotations

_FORBIDDEN_CHARS = frozenset('<>"')
_MAX_LEN = 200


def sanitize_utm(value: str | None) -> str | None:
    """Strip control chars + dangerous chars, truncate to 200.

    Returns ``None`` for ``None`` input AND for values that become
    empty after stripping (so we never persist whitespace-only or
    all-junk UTMs as if they were real attribution).
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None

    # Strip ASCII control chars (NUL through US, plus DEL) and the
    # three forbidden display chars in one pass.
    cleaned = "".join(
        ch
        for ch in value
        if ord(ch) >= 0x20 and ord(ch) != 0x7F and ch not in _FORBIDDEN_CHARS
    )
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_LEN:
        cleaned = cleaned[:_MAX_LEN]
    return cleaned
