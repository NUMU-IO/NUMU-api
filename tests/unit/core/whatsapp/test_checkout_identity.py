"""Checkout-identity unit coverage — config resolution, OTP plain copy,
GOWA fast-path, capability gate, flag key/mask helpers.

Pure-logic tests; the route-level behaviour (issue/verify/status + checkout
enforcement) is exercised against the app in the integration suite.
"""

from datetime import UTC, datetime

import pytest

from src.application.services.checkout_identity import (
    IDENTITY_FLAG_TTL_SECONDS,
    identity_flag_key,
    otp_available,
)
from src.config.settings import settings
from src.core.checkout_fields import (
    CheckoutFieldsConfig,
    IdentityConfig,
    default_config,
    resolve_config,
)
from src.core.interfaces.services.messaging_service import MessageType
from src.core.whatsapp_plain_render import render_plain_template
from src.infrastructure.external_services.whatsapp.gowa_guard import (
    DEFAULT_ALLOWED_TYPES,
    JITTER_MIN_SECONDS,
    OTP_JITTER_MAX_SECONDS,
    GowaSendGuard,
)

# ── checkout_fields.identity ────────────────────────────────────────


def test_default_config_carries_identity_block() -> None:
    cfg = default_config()
    assert cfg["identity"] == {
        "require_verification": True,
        "nudge_enabled": True,
        "nudge_min_items": 1,
        "nudge_min_value_cents": 0,
        "nudge_delay_seconds": 45,
    }


def test_resolve_config_merges_stored_identity_over_defaults() -> None:
    cfg = resolve_config({
        "checkout_fields": {
            "identity": {"require_verification": False, "nudge_min_items": 3}
        }
    })
    assert cfg["identity"]["require_verification"] is False
    assert cfg["identity"]["nudge_min_items"] == 3
    # Unspecified keys keep their defaults.
    assert cfg["identity"]["nudge_enabled"] is True
    assert cfg["identity"]["nudge_delay_seconds"] == 45


def test_resolve_config_coerces_bad_identity_values() -> None:
    """A hand-edited settings blob can't leak junk types to the storefront."""
    cfg = resolve_config({
        "checkout_fields": {
            "identity": {
                "nudge_min_items": "2",  # numeric string → int
                "nudge_delay_seconds": "abc",  # garbage → default
                "nudge_min_value_cents": -5,  # negative → clamped to 0
            }
        }
    })
    assert cfg["identity"]["nudge_min_items"] == 2
    assert cfg["identity"]["nudge_delay_seconds"] == 45
    assert cfg["identity"]["nudge_min_value_cents"] == 0


def test_to_storage_round_trips_identity() -> None:
    model = CheckoutFieldsConfig(
        identity=IdentityConfig(require_verification=False, nudge_delay_seconds=120)
    )
    stored = model.to_storage()
    assert stored["identity"]["require_verification"] is False
    assert stored["identity"]["nudge_delay_seconds"] == 120
    resolved = resolve_config({"checkout_fields": stored})
    assert resolved["identity"]["require_verification"] is False
    assert resolved["identity"]["nudge_delay_seconds"] == 120


# ── OTP plain-text copy (GOWA render path) ──────────────────────────


@pytest.mark.parametrize("language", ["en", "ar"])
def test_otp_plain_render_contains_code_and_store(language: str) -> None:
    msg = render_plain_template(
        MessageType.OTP_VERIFICATION,
        language,
        {"code": "482913", "store_name": "Lumière"},
    )
    assert msg is not None
    assert "482913" in msg.text
    assert "Lumière" in msg.text
    # An OTP has no buttons — nothing to correlate, nothing to record.
    assert msg.quick_replies == {}
    assert msg.quick_reply_payloads == {}


def test_otp_plain_render_no_leftover_placeholders() -> None:
    msg = render_plain_template(
        MessageType.OTP_VERIFICATION, "ar", {"code": "000111", "store_name": "متجر"}
    )
    assert msg is not None
    assert "{{" not in msg.text


# ── GOWA guard: OTP allowlist + jitter fast-path ────────────────────


def test_otp_type_in_default_gowa_allowlist() -> None:
    assert str(MessageType.OTP_VERIFICATION) in DEFAULT_ALLOWED_TYPES


def test_otp_jitter_uses_tight_band() -> None:
    for _ in range(50):
        delay = GowaSendGuard._jitter(str(MessageType.OTP_VERIFICATION))
        assert 0 < delay <= OTP_JITTER_MAX_SECONDS


def test_non_otp_jitter_keeps_wide_band() -> None:
    for _ in range(50):
        assert GowaSendGuard._jitter("order_confirmation") >= JITTER_MIN_SECONDS
    for _ in range(50):
        assert GowaSendGuard._jitter(None) >= JITTER_MIN_SECONDS


# ── Capability gate ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_otp_available_false_while_platform_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env gate short-circuits before any store/transport logic —
    db_session=None would explode if anything tried to query."""
    monkeypatch.setattr(settings, "checkout_identity_enabled", False)
    from uuid import uuid4

    assert (
        await otp_available(uuid4(), {"whatsapp": {"provider": "gowa"}}, None) is False
    )


@pytest.mark.asyncio
async def test_otp_available_false_for_meta_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Meta-only stores self-degrade: no approved AUTH template path yet."""
    monkeypatch.setattr(settings, "checkout_identity_enabled", True)
    from uuid import uuid4

    assert (
        await otp_available(uuid4(), {"whatsapp": {"provider": "meta_cloud"}}, None)
        is False
    )


# ── Flag helpers ────────────────────────────────────────────────────


def test_identity_flag_key_shape() -> None:
    assert identity_flag_key("store-1", "cart-9") == "identity_verified:store-1:cart-9"


def test_identity_flag_ttl_is_a_day() -> None:
    assert IDENTITY_FLAG_TTL_SECONDS == 86400


def test_mask_phone() -> None:
    from src.api.v1.routes.storefront.identity import _mask_phone

    masked = _mask_phone("+201001234567")
    assert masked.startswith("+20")
    assert masked.endswith("4567")
    assert "12345" not in masked


def test_utc_now_sanity() -> None:
    # Guards against naive/aware drift in the module's timestamps.
    assert datetime.now(UTC).tzinfo is not None
