"""Unit tests for the COD Autopilot digest reply grammar + payload routing.

FR-006/FR-007 (research R-04): the exceptions grammar is strict — an
optional keyword then digits with ``,``/``،``/space separators, ASCII or
Arabic-Indic. ANYTHING ambiguous parses to None so the caller changes
nothing. These tests are the contract that "no status changes on
ambiguity, ever" rests on.
"""

from __future__ import annotations

import pytest

from src.application.services.cod_autopilot_service import (
    _normalize_digits,
    parse_exceptions_reply,
)
from src.application.services.order_confirmation_service import (
    parse_quick_reply_action,
)

VALID = {1, 2, 3, 4, 5}


# ─── accepted forms ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("except 2, 5", [2, 5]),
        ("except 2,5", [2, 5]),
        ("Except 2 5", [2, 5]),
        ("EXCEPT 1", [1]),
        ("2, 5", [2, 5]),  # bare numbers, no keyword
        ("3", [3]),
        ("الا 2، 5", [2, 5]),  # Arabic keyword + Arabic comma
        ("إلا 2", [2]),
        ("ماعدا 1، 3", [1, 3]),
        ("ما عدا 4", [4]),
        ("بدون 5", [5]),
        ("except ٢، ٥", [2, 5]),  # Arabic-Indic digits
        ("٢ ٣", [2, 3]),
        ("except 2.5", [2, 5]),  # dot as separator (typo tolerance)
        ("except 2, 2, 5", [2, 5]),  # duplicates collapse
    ],
)
def test_parseable_replies(text, expected):
    assert parse_exceptions_reply(text, VALID) == expected


# ─── rejected forms → None → caller must no-op (FR-007) ───────────────


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "except",  # keyword with no numbers
        "except 9",  # out of range
        "except 0",  # not a valid item number
        "except 2, 9",  # ONE bad number poisons the whole reply
        "shipped all but the Alexandria one",  # natural language
        "except two",  # word numbers
        "ok",
        "تمام",
        "yes",
        "except 2 and 5",  # 'and' is not a separator
        "2-5",  # ranges not supported
    ],
)
def test_unparseable_replies_return_none(text):
    assert parse_exceptions_reply(text, VALID) is None


def test_empty_valid_set_rejects_everything():
    assert parse_exceptions_reply("except 1", set()) is None


def test_normalize_digits_arabic_indic_and_eastern():
    assert _normalize_digits("٢٥") == "25"
    assert _normalize_digits("۲۵") == "25"
    assert _normalize_digits("2ab٣") == "2ab3"


# ─── quick-reply action routing (T013) ────────────────────────────────


@pytest.mark.parametrize(
    "payload,action",
    [
        ("shipall:cairostyle/11111111-1111-1111-1111-111111111111", "shipall"),
        ("dlvyes:cairostyle/11111111-1111-1111-1111-111111111111", "dlvyes"),
        ("dlvnot:cairostyle/11111111-1111-1111-1111-111111111111", "dlvnot"),
        ("dlvref:cairostyle/11111111-1111-1111-1111-111111111111", "dlvref"),
        # Existing actions unaffected.
        ("confirm:cairostyle/11111111-1111-1111-1111-111111111111", "confirm"),
        ("postpone:x/11111111-1111-1111-1111-111111111111", "postpone"),
        ("cancel:x/11111111-1111-1111-1111-111111111111", "cancel"),
        # Legacy no-prefix payloads still default to confirm.
        ("cairostyle/11111111-1111-1111-1111-111111111111", "confirm"),
        # Unknown prefixes stay on the safe default.
        ("bogus:x/11111111-1111-1111-1111-111111111111", "confirm"),
    ],
)
def test_parse_quick_reply_action_routes_autopilot_payloads(payload, action):
    assert parse_quick_reply_action(payload) == action
