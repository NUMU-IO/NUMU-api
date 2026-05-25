"""Unit tests for the STOP-keyword detector.

Pure-logic — no I/O. Covers Latin case-insensitivity, Arabic tashkeel
normalization, first-word-only rule, and bidi-mark stripping.
"""

import pytest

from src.core.services.whatsapp_stop_keyword_detector import (
    is_stop_keyword,
    normalize,
)

# Arabic test strings constructed at runtime so the source file remains
# ASCII-only (bandit B613 trojan-source rule).
_ILGHA_HAMZA = (
    chr(0x0625) + chr(0x0644) + chr(0x063A) + chr(0x0627) + chr(0x0621)
)  # إلغاء
_ILGHA_NO_HAMZA = (
    chr(0x0627) + chr(0x0644) + chr(0x063A) + chr(0x0627) + chr(0x0621)
)  # الغاء
# Same word but with a kasra tashkeel on the alef — must still match
# after _TASHKEEL_RE strips it.
_ILGHA_WITH_KASRA = (
    chr(0x0625)
    + chr(0x0650)  # kasra
    + chr(0x0644)
    + chr(0x063A)
    + chr(0x0627)
    + chr(0x0621)
)

_LRM = chr(0x200E)
_RLM = chr(0x200F)
_LRE = chr(0x202A)


# ── Latin keywords ──────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["STOP", "stop", "Stop", " stop ", "stop please"])
def test_latin_stop_detected(text: str) -> None:
    assert is_stop_keyword(text)


@pytest.mark.parametrize("text", ["UNSUBSCRIBE", "unsubscribe", "Unsubscribe me"])
def test_latin_unsubscribe_detected(text: str) -> None:
    assert is_stop_keyword(text)


# ── Arabic keywords ─────────────────────────────────────────────────


def test_arabic_ilgha_with_hamza_detected() -> None:
    assert is_stop_keyword(_ILGHA_HAMZA)


def test_arabic_ilgha_no_hamza_detected() -> None:
    """Common dialectal spelling without hamza below alef."""
    assert is_stop_keyword(_ILGHA_NO_HAMZA)


def test_arabic_ilgha_with_tashkeel_detected() -> None:
    """Tashkeel marks must be stripped before comparison."""
    assert is_stop_keyword(_ILGHA_WITH_KASRA)


# ── First-word rule (spec edge case) ────────────────────────────────


def test_stop_in_middle_of_sentence_not_detected() -> None:
    """`please STOP sending` should route to the conversations inbox,
    not flip opt-out."""
    assert not is_stop_keyword("please STOP sending")


def test_stop_as_first_word_with_trailing_text_detected() -> None:
    assert is_stop_keyword("stop sending these messages")


# ── Bidi mark stripping ─────────────────────────────────────────────


def test_leading_lrm_does_not_block_detection() -> None:
    """Some clients prepend LRM (U+200E). Must be stripped before the
    first-word check."""
    assert is_stop_keyword(f"{_LRM}STOP")


def test_leading_rlm_does_not_block_detection() -> None:
    assert is_stop_keyword(f"{_RLM}{_ILGHA_HAMZA}")


def test_leading_embedding_mark_does_not_block_detection() -> None:
    assert is_stop_keyword(f"{_LRE}stop")


# ── Negative / boundary cases ───────────────────────────────────────


@pytest.mark.parametrize("text", [None, "", "   ", "\n\t"])
def test_empty_or_whitespace_returns_false(text: str | None) -> None:
    assert not is_stop_keyword(text)


def test_unrelated_message_not_detected() -> None:
    assert not is_stop_keyword("hi can you help me with my order")


def test_dialectal_not_detected_in_v1() -> None:
    """Dialectal 'batal' / 'waqf' explicitly excluded v1 per spec
    research R7. Add behind config later if real customer messages warrant.
    """
    # batal — bāʾ + ṭāʾ + lām
    batal = chr(0x0628) + chr(0x0637) + chr(0x0644)
    assert not is_stop_keyword(batal)


# ── normalize() directly ────────────────────────────────────────────


def test_normalize_strips_tashkeel() -> None:
    assert normalize(_ILGHA_WITH_KASRA) == _ILGHA_HAMZA


def test_normalize_strips_leading_bidi() -> None:
    assert normalize(f"{_LRM}{_RLM}{_LRE}hello") == "hello"


def test_normalize_idempotent() -> None:
    once = normalize("STOP")
    twice = normalize(once)
    assert once == twice
