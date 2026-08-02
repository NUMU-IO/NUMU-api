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

    due_renewal = await _mk_tenant(
        test_session, next_renewal_at=now + timedelta(days=3)
    )
    far_renewal = await _mk_tenant(
        test_session, next_renewal_at=now + timedelta(days=20)
    )
    already_due = await _mk_tenant(
        test_session, next_renewal_at=now - timedelta(hours=1)
    )
    internal = await _mk_tenant(
        test_session, next_renewal_at=now + timedelta(days=3), is_internal=True
    )
    warned_this_period = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=3),
        renewal_warning_sent_at=now - timedelta(hours=2),
    )
    warned_last_period = await _mk_tenant(
        test_session,
        next_renewal_at=now + timedelta(days=3),
        renewal_warning_sent_at=now - timedelta(days=40),
    )
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
    assert by_id.get(str(due_trial.id)) == "trial"
    assert str(far_renewal.id) not in by_id
    assert str(already_due.id) not in by_id  # past anchor → renewal task owns it
    assert str(internal.id) not in by_id
    assert str(warned_this_period.id) not in by_id
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
    tenant = await _mk_tenant(test_session, next_renewal_at=now + timedelta(days=10))

    cfg = await get_billing_settings(test_session, use_cache=False)
    targets = await _collect_warning_targets(test_session, cfg, now)
    assert str(tenant.id) not in {str(t.id) for t, _k, _a in targets}

    # Widen the window to 14 days → the same tenant is now due a warning.
    await update_billing_settings(test_session, {"renewal_warning_days": 14})
    await test_session.commit()
    cfg = await get_billing_settings(test_session, use_cache=False)
    targets = await _collect_warning_targets(test_session, cfg, now)
    assert str(tenant.id) in {str(t.id) for t, _k, _a in targets}
