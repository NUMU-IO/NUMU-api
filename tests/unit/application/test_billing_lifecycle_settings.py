"""Unit tests — billing lifecycle settings + pre-expiry warning selection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.application.services.billing_settings import (
    get_billing_settings,
    invalidate_billing_settings_cache,
    update_billing_settings,
)
from src.infrastructure.database.models.public.subscription_payment import (
    SubscriptionPaymentIntentModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.messaging.tasks.subscription_payment_tasks import (
    _collect_warning_targets,
)


@pytest.fixture(autouse=True)
def _fresh_settings():
    invalidate_billing_settings_cache()
    yield
    invalidate_billing_settings_cache()


async def _mk_tenant(session, **kw) -> TenantModel:
    tenant = TenantModel(
        id=uuid4(),
        name="Lifecycle Tenant",
        subdomain=f"lc-{uuid4().hex[:8]}",
        plan=kw.pop("plan", "starter"),
        lifecycle_state=kw.pop("lifecycle_state", "active"),
        **kw,
    )
    session.add(tenant)
    await session.commit()
    return tenant


# ─── Settings service ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_defaults_override_clamp_and_clear(test_session):
    cfg = await get_billing_settings(test_session, use_cache=False)
    assert cfg.renewal_warning_days == 7
    assert cfg.trial_warning_days == 5
    assert cfg.dunning_max_retries == 3
    assert cfg.dunning_retry_backoff_hours == 24
    assert cfg.dunning_window_hours == 72
    assert cfg.warning_emails_enabled is True

    merged = await update_billing_settings(
        test_session,
        {
            "renewal_warning_days": 3,
            "dunning_max_retries": 99,  # clamped to 10
            "warning_emails_enabled": False,
            "not_a_field": "ignored",
        },
    )
    await test_session.commit()
    assert merged.renewal_warning_days == 3
    assert merged.dunning_max_retries == 10
    assert merged.warning_emails_enabled is False

    # None clears the override back to the code default.
    merged = await update_billing_settings(test_session, {"renewal_warning_days": None})
    await test_session.commit()
    assert merged.renewal_warning_days == 7


# ─── Warning target selection ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_warning_targets_windows_and_dedup(test_session):
    now = datetime.now(UTC)
    cfg = await get_billing_settings(test_session, use_cache=False)

    # Renewal-warning candidates carry a card token — tenants with no
    # funding source are grandfathered out (see below).
    due_renewal = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=3),
        paymob_card_token_encrypted="tok",
    )
    far_renewal = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=20),
        paymob_card_token_encrypted="tok",
    )
    already_due = await _mk_tenant(
        test_session,
        next_renewal_at=now - timedelta(hours=1),
        paymob_card_token_encrypted="tok",
    )
    internal = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=3),
        is_internal=True,
        paymob_card_token_encrypted="tok",
    )
    warned_this_period = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=3),
        renewal_warning_sent_at=now - timedelta(hours=2),
        paymob_card_token_encrypted="tok",
    )
    warned_last_period = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=3),
        renewal_warning_sent_at=now - timedelta(days=40),
        paymob_card_token_encrypted="tok",
    )
    # GRANDFATHER: legacy tenant, no token, never paid via InstaPay —
    # must NOT suddenly get renewal emails after the rollout.
    legacy_no_token = await _mk_tenant(
        test_session, next_renewal_at=now + timedelta(days=3)
    )
    # Opt-in: no token but a succeeded InstaPay payment → warned.
    instapay_payer = await _mk_tenant(
        test_session, next_renewal_at=now + timedelta(days=3)
    )
    test_session.add(
        SubscriptionPaymentIntentModel(
            tenant_id=instapay_payer.id,
            plan_key="starter",
            billing_cycle="monthly",
            purpose="new_subscription",
            amount_cents=25_000,
            currency="EGP",
            status="succeeded",
            special_reference=f"SUB-{uuid4().hex[:6].upper()}",
        )
    )
    await test_session.commit()
    due_trial = await _mk_tenant(
        test_session,
        plan="trial",
        lifecycle_state="trial",
        expires_at=now + timedelta(days=2),
    )
    far_trial = await _mk_tenant(
        test_session,
        plan="trial",
        lifecycle_state="trial",
        expires_at=now + timedelta(days=20),
    )

    targets = await _collect_warning_targets(test_session, cfg, now)
    by_id = {str(t.id): kind for t, kind, _anchor in targets}

    assert by_id.get(str(due_renewal.id)) == "renewal"
    assert by_id.get(str(warned_last_period.id)) == "renewal"  # re-armed
    assert by_id.get(str(instapay_payer.id)) == "renewal"  # opted in by paying
    assert by_id.get(str(due_trial.id)) == "trial"
    assert str(far_renewal.id) not in by_id
    assert str(already_due.id) not in by_id  # past anchor → renewal task owns it
    assert str(internal.id) not in by_id
    assert str(warned_this_period.id) not in by_id
    assert str(legacy_no_token.id) not in by_id  # grandfathered
    assert str(far_trial.id) not in by_id

    # Stamping re-runs to empty for those tenants (per-period dedup).
    for t, _kind, _anchor in targets:
        t.renewal_warning_sent_at = now
    await test_session.commit()
    targets2 = await _collect_warning_targets(test_session, cfg, now)
    assert not targets2


@pytest.mark.asyncio
async def test_collect_warning_targets_respects_admin_window(test_session):
    now = datetime.now(UTC)
    tenant = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=10),
        paymob_card_token_encrypted="tok",
    )

    cfg = await get_billing_settings(test_session, use_cache=False)
    targets = await _collect_warning_targets(test_session, cfg, now)
    assert str(tenant.id) not in {str(t.id) for t, _k, _a in targets}

    # Widen the window to 14 days → the same tenant is now due a warning.
    await update_billing_settings(test_session, {"renewal_warning_days": 14})
    await test_session.commit()
    cfg = await get_billing_settings(test_session, use_cache=False)
    targets = await _collect_warning_targets(test_session, cfg, now)
    assert str(tenant.id) in {str(t.id) for t, _k, _a in targets}


@pytest.mark.asyncio
async def test_merchant_reminder_prefs_override_and_optout(test_session):
    """Per-tenant reminder prefs: a wider merchant window beats the
    platform default, and optout silences the reminder entirely."""
    now = datetime.now(UTC)
    cfg = await get_billing_settings(test_session, use_cache=False)
    assert cfg.renewal_warning_days == 7

    # 10 days out — outside the 7-day platform window, inside the
    # merchant's own 14-day preference.
    early_bird = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=10),
        paymob_card_token_encrypted="tok",
        renewal_reminder_days=14,
    )
    # 3 days out but opted out — never selected.
    opted_out = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=3),
        paymob_card_token_encrypted="tok",
        renewal_reminder_optout=True,
    )

    targets = await _collect_warning_targets(test_session, cfg, now)
    ids = {str(t.id) for t, _k, _a in targets}
    assert str(early_bird.id) in ids
    assert str(opted_out.id) not in ids


@pytest.mark.asyncio
async def test_grandfather_guard_instapay_opt_in_signal(test_session):
    """The renewal sweep's skip-vs-dun signal: a succeeded InstaPay
    intent opts a token-less tenant into the new lifecycle; anything
    less keeps the historical skip."""
    from src.infrastructure.messaging.tasks.subscription_renewal_task import (
        _has_succeeded_instapay_payment,
    )

    tenant = await _mk_tenant(test_session)
    assert await _has_succeeded_instapay_payment(test_session, tenant.id) is False

    pending = SubscriptionPaymentIntentModel(
        tenant_id=tenant.id,
        plan_key="starter",
        billing_cycle="monthly",
        purpose="new_subscription",
        amount_cents=25_000,
        currency="EGP",
        status="under_review",
        special_reference=f"SUB-{uuid4().hex[:6].upper()}",
    )
    test_session.add(pending)
    await test_session.commit()
    assert await _has_succeeded_instapay_payment(test_session, tenant.id) is False

    pending.status = "succeeded"
    await test_session.commit()
    assert await _has_succeeded_instapay_payment(test_session, tenant.id) is True
