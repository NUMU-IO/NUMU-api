"""STOP keyword detection for inbound WhatsApp messages.

Pure logic - no I/O. The detector inspects the first whitespace-delimited
token of an inbound message after Unicode NFKC normalization and Arabic
tashkeel stripping, and returns True iff the token is one of the canonical
opt-out keywords (FR-009).

Canonical set:
    - "stop" / "STOP"
    - "unsubscribe" / "UNSUBSCRIBE"
    - U+0625 U+0644 U+063A U+0627 U+0621        (ilgha, with hamza below alef)
    - U+0627 U+0644 U+063A U+0627 U+0621        (ilgha, without hamza)

Case-insensitive for Latin; Arabic compared after tashkeel removal.

Dialectal Arabic variants are intentionally excluded v1 - high
false-positive risk in conversational Arabic. Add behind a per-store
config later if real customer messages warrant it (see research.md R7).

Source-file note: all non-ASCII characters in this module are constructed
from ``chr()`` of explicit Unicode code points. The source file remains
ASCII-only so bandit B613 (trojan-source) cannot flag it.
"""

import re
import unicodedata

# ── Arabic opt-out tokens (built from chr() so source is ASCII-only) ──
# These are the only two opt-out spellings of "ilgha" we accept.

_ARABIC_ILGHA_WITH_HAMZA = (
    chr(0x0625)  # alef with hamza below
    + chr(0x0644)  # lam
    + chr(0x063A)  # ghain
    + chr(0x0627)  # alef
    + chr(0x0621)  # hamza
)

_ARABIC_ILGHA_NO_HAMZA = (
    chr(0x0627)  # alef
    + chr(0x0644)  # lam
    + chr(0x063A)  # ghain
    + chr(0x0627)  # alef
    + chr(0x0621)  # hamza
)

_OPT_OUT_TOKENS: frozenset[str] = frozenset({
    "stop",
    "unsubscribe",
    _ARABIC_ILGHA_WITH_HAMZA,
    _ARABIC_ILGHA_NO_HAMZA,
})

# ── Tashkeel-stripping regex ──────────────────────────────────────────
# Range covers U+064B..U+0652 (fathatan..sukun), U+0670 (superscript alef),
# and U+06D6..U+06ED (Quranic marks). Built from chr() ranges so the
# source remains ASCII.
_TASHKEEL_RE = re.compile(
    "["
    + chr(0x064B)
    + "-"
    + chr(0x0652)
    + chr(0x0670)
    + chr(0x06D6)
    + "-"
    + chr(0x06ED)
    + "]"
)

# ── Bidi / control mark stripper ──────────────────────────────────────
# Covers U+200E LRM, U+200F RLM, U+202A..U+202E embedding/override marks,
# U+2066..U+2069 isolate marks. Plus standard whitespace ('\s').
_BIDI_RANGE = (
    chr(0x200E)
    + chr(0x200F)
    + chr(0x202A)
    + "-"
    + chr(0x202E)
    + chr(0x2066)
    + "-"
    + chr(0x2069)
)
_LEADING_PUNCT_RE = re.compile(r"^[\s" + _BIDI_RANGE + "]+")


def normalize(text: str) -> str:
    """Apply NFKC + tashkeel-strip + leading-bidi-mark strip.

    Lowercase is NOT applied here; call sites lowercase separately for
    Latin comparison.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _TASHKEEL_RE.sub("", text)
    text = _LEADING_PUNCT_RE.sub("", text)
    return text.strip()


def is_stop_keyword(text: str | None) -> bool:
    """Return True iff the first word of ``text`` is a canonical STOP keyword.

    Cases handled:
    - ``"STOP"``                                -> True
    - ``"  stop  please"``                      -> True (case-insensitive, first word)
    - ``"please STOP sending"``                 -> False (not the first word; routes
                                                   to the conversations inbox normally
                                                   per the spec's edge cases)
    - Arabic ``ilgha`` (either spelling, with or without tashkeel) -> True
    - ``None`` / empty / whitespace-only        -> False
    """
    if not text:
        return False
    normalized = normalize(text)
    if not normalized:
        return False
    first = normalized.split(None, 1)[0]
    return first.lower() in _OPT_OUT_TOKENS or first in _OPT_OUT_TOKENS
