"""Back in Stock rules (BIS-U-01, 02, 04, 05, 06, 08, 11, 12, 13, 14)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.services import back_in_stock as bis

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


# BIS-U-01
@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("01012345678", "+201012345678"),
        ("0101 234 5678", "+201012345678"),
        ("010-1234-5678", "+201012345678"),
        ("+201112345678", "+201112345678"),
        ("00201212345678", "+201212345678"),
        ("201512345678", "+201512345678"),
        ("1012345678", "+201012345678"),
        ("٠١٠١٢٣٤٥٦٧٨", "+201012345678"),
        ("0225551234", None),  # Cairo landline
        ("01312345678", None),  # no such mobile prefix
        ("0101234567", None),  # too short
        ("+966501234567", None),  # Saudi
        ("", None),
        (None, None),
    ],
)
def test_egyptian_mobiles(raw, want):
    assert bis.normalize_phone(raw) == want


# BIS-U-02
@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("  Mona@Gmail.COM ", "mona@gmail.com"),
        ("not-an-email", None),
        ("a@b", None),
        ("x" * 245 + "@gmail.com", None),  # 255 characters
        ("x" * 244 + "@gmail.com", "x" * 244 + "@gmail.com"),  # 254
    ],
)
def test_emails(raw, want):
    assert bis.normalize_email(raw) == want


# BIS-U-11
def test_masking():
    assert bis.mask("+201012344567") == "+20 10•• ••• 4567"
    assert bis.mask("mohamed@gmail.com") == "m•••@gmail.com"
    assert bis.mask(None) == "—"


# BIS-U-04
def _variant(qty, track=True):
    return SimpleNamespace(id=uuid4(), inventory_quantity=qty, track_inventory=track)


@pytest.mark.parametrize(
    ("status", "variant", "want"),
    [
        ("active", _variant(3), True),
        ("unlisted", _variant(1), True),
        ("active", _variant(0, track=False), True),  # not tracked
        ("active", _variant(0), False),
        ("draft", _variant(5), False),
        ("archived", _variant(5), False),
    ],
)
def test_buyable_rule(status, variant, want):
    product = SimpleNamespace(status=status)
    assert bis.is_buyable(product, [variant], variant.id) is want


def test_selling_past_zero_alone_is_not_back():
    """continue_selling makes the PDP say "in stock"; the app still waits for
    real stock."""
    product = SimpleNamespace(
        status="active", attributes={"continue_selling_when_out_of_stock": True}
    )
    v = _variant(0)
    assert bis.is_buyable(product, [v], v.id) is False


def test_without_a_variant_any_buyable_variant_counts():
    product = SimpleNamespace(status="active")
    assert bis.is_buyable(product, [_variant(0), _variant(2)], None)
    assert not bis.is_buyable(product, [_variant(0), _variant(0)], None)


# BIS-U-05
@pytest.mark.parametrize(
    ("units", "room", "want"),
    [
        (None, 1000, 20),
        (0, 1000, 20),
        (1, 1000, 20),
        (3, 1000, 30),
        (50, 1000, 500),
        (50, 120, 120),
        (3, 0, 0),
    ],
)
def test_alert_cap(units, room, want):
    assert bis.alert_cap(units, room_today=room) == want


# BIS-U-06
def test_pacing_starts_after_30s_and_never_passes_10_a_minute():
    times = bis.send_countdowns(25)
    assert times[0] >= 30
    for start in range(times[0], times[-1] + 1):
        assert sum(start <= t < start + 60 for t in times) <= 10


# BIS-U-08
def test_tokens_are_22_char_base64url_and_unique():
    tokens = {bis.new_token() for _ in range(1000)}
    assert len(tokens) == 1000
    assert all(
        len(t) == 22 and t.replace("-", "a").replace("_", "a").isalnum() for t in tokens
    )


def test_destinations():
    plain = SimpleNamespace(custom_domain=None, subdomain="vionne")
    custom = SimpleNamespace(custom_domain="vionneeg.com/", subdomain="vionne")
    vid = uuid4()

    assert bis.product_url(plain, "shirt", vid, "whatsapp") == (
        f"https://vionne.numueg.app/products/shirt?variant={vid}"
        "&utm_source=numu_back_in_stock&utm_medium=whatsapp"
    )
    assert bis.product_url(custom, "bag", None, "email").startswith(
        "https://vionneeg.com/products/bag?utm_source=numu_back_in_stock"
    )
    assert (
        bis.unsubscribe_url(custom, "t")
        == "https://vionneeg.com/unsubscribe/back-in-stock/t"
    )
    assert bis.button_values("vionne", "a", "b") == (
        "back-in-stock/vionne/a",
        "back-in-stock/vionne/u/b",
    )


# BIS-U-14
@pytest.mark.parametrize(
    ("slug", "version", "want"),
    [
        ("vionne-v3", "0.11.20", True),
        ("vionne-v3", "0.11.21+mp.1791508650.ab12cd34", True),
        ("vionne-v3", "0.11.19", False),
        ("vionne-v3", "0.12.0", True),
        ("unknown-v3", "9.9.9", False),
        ("vionne-v3", None, False),
    ],
)
def test_theme_readiness(monkeypatch, slug, version, want):
    monkeypatch.setattr(bis, "SLOT_READY_THEME_VERSIONS", {"vionne-v3": "0.11.20"})
    assert bis.theme_ready(slug, version) is want


# BIS-U-12
def test_attribution_window_and_matching_line():
    p, v, other = uuid4(), uuid4(), uuid4()
    lines = [bis.OrderLine(p, v, 25000), bis.OrderLine(other, None, 9000)]

    def at(days, variant=v, ls=lines):
        return bis.attributed_revenue(
            notified_at=NOW,
            ordered_at=NOW + timedelta(days=days),
            product_id=p,
            variant_id=variant,
            lines=ls,
        )

    assert at(0) == 25000
    assert at(7) == 25000
    assert at(8) is None
    assert at(1, variant=uuid4()) is None  # another variant of the product
    assert at(1, variant=None) == 25000  # waited for the product, any variant
    assert at(1, ls=[bis.OrderLine(other, None, 9000)]) is None
    assert at(-1) is None  # ordered before the alert


# BIS-U-13
@pytest.mark.parametrize(
    ("status", "age_days", "since_change", "has_contact", "want"),
    [
        ("waiting", 181, 181, True, ("closed", False)),
        ("waiting", 179, 179, True, (None, False)),
        ("notified", 40, 31, True, (None, True)),
        ("notified", 40, 29, True, (None, False)),
        ("unsubscribed", 40, 31, True, (None, True)),
        ("closed", 200, 31, False, (None, False)),  # already erased
        ("queued", 40, 31, True, (None, False)),  # mid-send: never erased
    ],
)
def test_retention(status, age_days, since_change, has_contact, want):
    assert (
        bis.retention_actions(
            status=status,
            created_at=NOW - timedelta(days=age_days),
            updated_at=NOW - timedelta(days=since_change),
            has_contact=has_contact,
            now=NOW,
        )
        == want
    )


def test_settings_default_without_a_row():
    assert bis.merged_settings(None) == bis.DEFAULT_SETTINGS
    row = SimpleNamespace(contact="phone", signup_cap=10, wa_cap=5, email_cap=7)
    assert bis.merged_settings(row) == {
        "contact": "phone",
        "signup_cap": 10,
        "wa_cap": 5,
        "email_cap": 7,
    }
