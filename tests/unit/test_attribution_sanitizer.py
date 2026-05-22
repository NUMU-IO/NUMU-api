"""Unit tests for ``attribution_sanitizer.sanitize_utm``."""

from __future__ import annotations

import pytest

from src.application.services.attribution_sanitizer import sanitize_utm

# ── Passthrough cases ───────────────────────────────────────────────


def test_sanitize_preserves_clean_utm():
    assert sanitize_utm("facebook") == "facebook"


def test_sanitize_preserves_allowed_special_chars():
    """Common UTM patterns include hyphens, underscores, %-encoded etc."""
    assert sanitize_utm("eid-sale-2026-AB7K") == "eid-sale-2026-AB7K"
    assert sanitize_utm("mailchimp_campaign_xyz") == "mailchimp_campaign_xyz"
    assert sanitize_utm("source%20with%20space") == "source%20with%20space"
    assert sanitize_utm("tag+plus") == "tag+plus"
    assert sanitize_utm("a:b/c.d") == "a:b/c.d"


def test_sanitize_passes_none_through():
    assert sanitize_utm(None) is None


def test_sanitize_passes_non_string_as_none():
    """Defensive: caller might pass an int from a JSON-decoded body."""
    assert sanitize_utm(42) is None  # type: ignore[arg-type]
    assert sanitize_utm([1, 2]) is None  # type: ignore[arg-type]


# ── Strip rules ─────────────────────────────────────────────────────


def test_sanitize_strips_control_chars():
    """\\x00–\\x1F and \\x7F break CSV exports."""
    assert sanitize_utm("face\x00book") == "facebook"
    assert sanitize_utm("face\x07book") == "facebook"  # bell
    assert sanitize_utm("face\x1fbook") == "facebook"  # unit separator
    assert sanitize_utm("face\x7fbook") == "facebook"  # DEL


def test_sanitize_keeps_tab_newline_as_dropped_control():
    """\\t and \\n are control chars (0x09, 0x0A) — also dropped."""
    assert sanitize_utm("face\tbook") == "facebook"
    assert sanitize_utm("face\nbook") == "facebook"


def test_sanitize_strips_forbidden_display_chars():
    """<, >, " indicate tampering or a bad copy-paste."""
    assert sanitize_utm("face<book") == "facebook"
    assert sanitize_utm("face>book") == "facebook"
    assert sanitize_utm('face"book') == "facebook"
    assert sanitize_utm('<script>alert("x")</script>') == "scriptalert(x)/script"


def test_sanitize_trims_surrounding_whitespace():
    assert sanitize_utm("  facebook  ") == "facebook"


def test_sanitize_returns_none_for_empty_after_strip():
    """All-junk inputs become empty → None, not empty string."""
    assert sanitize_utm("") is None
    assert sanitize_utm("   ") is None
    assert sanitize_utm("\x00\x01\x02") is None
    assert sanitize_utm("<>>") is None


# ── Length cap ──────────────────────────────────────────────────────


def test_sanitize_truncates_to_200_chars():
    value = "a" * 250
    out = sanitize_utm(value)
    assert out is not None
    assert len(out) == 200
    assert out == "a" * 200


def test_sanitize_truncates_after_cleaning():
    """Length cap applies to the post-strip string, not the raw input."""
    # 100 'a's, then 200 control chars (all stripped), then 100 'b's.
    # After strip: 200 chars of "a"*100 + "b"*100 — fits in 200, no trunc.
    raw = "a" * 100 + ("\x00" * 200) + "b" * 100
    assert sanitize_utm(raw) == "a" * 100 + "b" * 100


def test_sanitize_truncates_long_clean_input_to_200():
    raw = "x" * 199 + "yz"  # 201 chars, all clean
    out = sanitize_utm(raw)
    assert out is not None
    assert len(out) == 200


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Facebook", "Facebook"),
        ("FACEBOOK", "FACEBOOK"),
        ("face\x00book", "facebook"),
        ("face<book", "facebook"),
        (None, None),
        ("", None),
    ],
)
def test_sanitize_parametrized(raw, expected):
    assert sanitize_utm(raw) == expected
