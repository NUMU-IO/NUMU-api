"""Unit tests — signup/trial settings + public pricing payg/trial injection."""

from __future__ import annotations

import pytest

from src.api.v1.routes.public.landing import get_public_pricing_plans
from src.application.services.signup_settings import (
    get_signup_settings,
    invalidate_signup_settings_cache,
    update_signup_settings,
)
from src.application.services.wallet_settings import (
    invalidate_wallet_settings_cache,
    update_wallet_settings,
)


@pytest.fixture(autouse=True)
def _fresh_settings_caches():
    invalidate_signup_settings_cache()
    invalidate_wallet_settings_cache()
    yield
    invalidate_signup_settings_cache()
    invalidate_wallet_settings_cache()


@pytest.mark.asyncio
async def test_signup_settings_defaults_and_override(test_session):
    signup = await get_signup_settings(test_session, use_cache=False)
    assert signup.trial_enabled is True
    assert signup.trial_days == 37
    assert signup.payg_visible_on_landing is True

    merged = await update_signup_settings(
        test_session,
        {"trial_days": 14, "trial_visible_on_landing": False, "junk": 1},
    )
    await test_session.commit()
    assert merged.trial_days == 14
    assert merged.trial_visible_on_landing is False

    # None clears back to default.
    merged = await update_signup_settings(test_session, {"trial_days": None})
    await test_session.commit()
    assert merged.trial_days == 37


@pytest.mark.asyncio
async def test_public_pricing_includes_payg_and_trial_meta(test_session):
    resp = await get_public_pricing_plans(test_session)
    data = resp.data

    keys = [p["key"] for p in data["plans"]]
    assert "payg" in keys
    # Injected right after the trial card.
    assert keys.index("payg") == keys.index("trial") + 1

    payg = next(p for p in data["plans"] if p["key"] == "payg")
    assert payg["cta"] == "signup_payg"
    assert payg["commission_percent"] == 3.0  # plan default 300 bps
    assert payg["price_monthly"] == 0

    assert data["trial"] == {"enabled": True, "days": 37, "visible": True}


@pytest.mark.asyncio
async def test_public_pricing_reflects_admin_rate_and_visibility(test_session):
    # Admin sets the wallet default rate to 2.5% → landing shows 2.5%.
    await update_wallet_settings(test_session, {"commission_bps_default": 250})
    await test_session.commit()

    resp = await get_public_pricing_plans(test_session)
    payg = next(p for p in resp.data["plans"] if p["key"] == "payg")
    assert payg["commission_percent"] == 2.5
    assert any("2.5%" in f["en"] for f in payg["features"])

    # Hiding payg removes the card entirely.
    await update_signup_settings(test_session, {"payg_visible_on_landing": False})
    await test_session.commit()
    resp = await get_public_pricing_plans(test_session)
    assert all(p["key"] != "payg" for p in resp.data["plans"])


@pytest.mark.asyncio
async def test_public_pricing_hides_trial_when_disabled(test_session):
    await update_signup_settings(test_session, {"trial_enabled": False})
    await test_session.commit()

    resp = await get_public_pricing_plans(test_session)
    assert all(p["key"] != "trial" for p in resp.data["plans"])
    assert resp.data["trial"]["enabled"] is False
    assert resp.data["trial"]["visible"] is False
