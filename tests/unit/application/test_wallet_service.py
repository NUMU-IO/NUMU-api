"""Unit tests — WalletService ledger primitive + gate + warning ladder.

Runs on the in-memory SQLite test engine; the conftest metadata patch
translates the Postgres partial unique indexes (``postgresql_where``) to
their SQLite equivalents so the (order_id, kind) idempotency backbone is
exercised for real.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.services.wallet_service import (
    WalletService,
    WalletSuspendedError,
    warning_level_for,
)
from src.application.services.wallet_settings import (
    invalidate_wallet_settings_cache,
)
from src.core.entities.wallet import WalletTransactionKind
from src.infrastructure.database.models.public.tenant import TenantModel


@pytest.fixture(autouse=True)
def _fresh_wallet_settings():
    """The admin-settings TTL cache must never leak between tests."""
    invalidate_wallet_settings_cache()
    yield
    invalidate_wallet_settings_cache()


async def _mk_tenant(session, plan: str = "payg") -> TenantModel:
    tenant = TenantModel(
        id=uuid4(),
        name="Payg Tenant",
        subdomain=f"payg-{uuid4().hex[:8]}",
        plan=plan,
        lifecycle_state="active",
    )
    session.add(tenant)
    await session.commit()
    return tenant


@pytest.mark.asyncio
async def test_apply_entry_credits_and_debits(test_session):
    tenant = await _mk_tenant(test_session)
    service = WalletService(test_session, cache=None)

    topup = await service.apply_entry(
        tenant_id=tenant.id,
        kind=WalletTransactionKind.TOPUP,
        amount_cents=10_000,
        idempotency_key="paymob:tx1",
    )
    assert topup is not None
    assert topup.balance_after_cents == 10_000

    commission = await service.apply_entry(
        tenant_id=tenant.id,
        kind=WalletTransactionKind.COMMISSION,
        amount_cents=-300,
        order_id=uuid4(),
    )
    assert commission is not None
    assert commission.balance_after_cents == 9_700

    wallet = await service.get_or_create_wallet(tenant.id)
    assert wallet.balance_cents == 9_700


@pytest.mark.asyncio
async def test_duplicate_commission_for_same_order_is_noop(test_session):
    tenant = await _mk_tenant(test_session)
    service = WalletService(test_session, cache=None)
    order_id = uuid4()

    first = await service.apply_entry(
        tenant_id=tenant.id,
        kind=WalletTransactionKind.COMMISSION,
        amount_cents=-500,
        order_id=order_id,
    )
    assert first is not None

    # Same order, same kind → partial unique index absorbs it.
    dup = await service.apply_entry(
        tenant_id=tenant.id,
        kind=WalletTransactionKind.COMMISSION,
        amount_cents=-500,
        order_id=order_id,
    )
    assert dup is None

    wallet = await service.get_or_create_wallet(tenant.id)
    assert wallet.balance_cents == -500  # charged exactly once

    # A reversal for the same order is a DIFFERENT kind — must succeed.
    reversal = await service.apply_entry(
        tenant_id=tenant.id,
        kind=WalletTransactionKind.COMMISSION_REVERSAL,
        amount_cents=500,
        order_id=order_id,
    )
    assert reversal is not None
    assert reversal.balance_after_cents == 0

    # ...but only once.
    dup_reversal = await service.apply_entry(
        tenant_id=tenant.id,
        kind=WalletTransactionKind.COMMISSION_REVERSAL,
        amount_cents=500,
        order_id=order_id,
    )
    assert dup_reversal is None


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_is_noop(test_session):
    tenant = await _mk_tenant(test_session)
    service = WalletService(test_session, cache=None)

    first = await service.apply_entry(
        tenant_id=tenant.id,
        kind=WalletTransactionKind.TOPUP,
        amount_cents=5_000,
        idempotency_key="paymob:tx42",
    )
    assert first is not None
    dup = await service.apply_entry(
        tenant_id=tenant.id,
        kind=WalletTransactionKind.TOPUP,
        amount_cents=5_000,
        idempotency_key="paymob:tx42",
    )
    assert dup is None
    wallet = await service.get_or_create_wallet(tenant.id)
    assert wallet.balance_cents == 5_000


@pytest.mark.asyncio
async def test_suspended_wallet_rejects_writes(test_session):
    tenant = await _mk_tenant(test_session)
    service = WalletService(test_session, cache=None)
    wallet = await service.get_or_create_wallet(tenant.id)
    wallet.status = "suspended"
    await test_session.commit()

    with pytest.raises(WalletSuspendedError):
        await service.apply_entry(
            tenant_id=tenant.id,
            kind=WalletTransactionKind.TOPUP,
            amount_cents=1_000,
        )


@pytest.mark.asyncio
async def test_effective_commission_bps_resolution(test_session):
    tenant = await _mk_tenant(test_session, plan="payg")
    service = WalletService(test_session, cache=None)
    wallet = await service.get_or_create_wallet(tenant.id)

    # Plan rate (payg = 300 bps).
    assert service.effective_commission_bps(tenant, wallet) == 300
    # Per-tenant override wins.
    wallet.commission_bps_override = 150
    assert service.effective_commission_bps(tenant, wallet) == 150
    # Exempt kills it entirely, override or not.
    wallet.status = "exempt"
    assert service.effective_commission_bps(tenant, wallet) == 0
    # Subscription plans are always 0.
    tenant.plan = "starter"
    assert service.effective_commission_bps(tenant, None) == 0


def test_warning_ladder_levels():
    kw = {"negative_allowance_cents": 5_000, "low_threshold_cents": 10_000}
    assert warning_level_for(50_000, **kw) == 0
    assert warning_level_for(10_000, **kw) == 0
    assert warning_level_for(9_999, **kw) == 1
    assert warning_level_for(0, **kw) == 1
    assert warning_level_for(-1, **kw) == 2
    assert warning_level_for(-5_000, **kw) == 2
    assert warning_level_for(-5_001, **kw) == 3


@pytest.mark.asyncio
async def test_bump_warning_level_dedupes_and_recovers(test_session):
    tenant = await _mk_tenant(test_session)
    service = WalletService(test_session, cache=None)
    wallet = await service.get_or_create_wallet(tenant.id)

    wallet.balance_cents = -100  # level 2
    assert service.bump_warning_level(wallet) == 2
    assert service.bump_warning_level(wallet) is None  # same level → no re-notify
    wallet.balance_cents = -100_000  # deep below allowance → level 3
    assert service.bump_warning_level(wallet) == 3
    wallet.balance_cents = 50_000  # recovered
    assert service.bump_warning_level(wallet) is None
    assert wallet.last_warning_level == 0  # ladder reset for next dip


@pytest.mark.asyncio
async def test_checkout_gate(test_session, monkeypatch):
    from src.config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ff_wallet_checkout_gate", True)

    tenant = await _mk_tenant(test_session, plan="payg")
    service = WalletService(test_session, cache=None)
    # `cache=None` only means "no cache injected" — the constructor still
    # attaches Redis whenever redis_host is configured, and the gate answer is
    # cached for 60s. This test mutates balances directly (no apply_entry, so
    # no invalidate_cache), so drop the cache to read the gate state itself.
    service._cache = None
    wallet = await service.get_or_create_wallet(tenant.id)
    await test_session.commit()

    # Zero balance is within the allowance → allowed.
    assert await service.checkout_gate_allows(tenant.id) is True

    # Below the negative allowance → blocked.
    wallet.balance_cents = -10_000
    wallet.negative_allowance_cents = 5_000
    await test_session.commit()
    assert await service.checkout_gate_allows(tenant.id) is False

    # Subscription tenant with the same balance → never blocked.
    tenant.plan = "starter"
    await test_session.commit()
    assert await service.checkout_gate_allows(tenant.id) is True


@pytest.mark.asyncio
async def test_checkout_gate_flag_off_never_blocks(test_session, monkeypatch):
    from src.config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ff_wallet_checkout_gate", False)

    tenant = await _mk_tenant(test_session, plan="payg")
    service = WalletService(test_session, cache=None)
    service._cache = None  # see test_checkout_gate — cache=None still uses Redis
    wallet = await service.get_or_create_wallet(tenant.id)
    wallet.balance_cents = -1_000_000
    await test_session.commit()

    assert await service.checkout_gate_allows(tenant.id) is True

    # ...unless the per-tenant canary flag arms it.
    tenant.feature_flags = {"wallet_checkout_gate": True}
    await test_session.commit()
    assert await service.checkout_gate_allows(tenant.id) is False


@pytest.mark.asyncio
async def test_checkout_gate_fails_open_on_error(test_session):
    class ExplodingSession:
        def __getattr__(self, name):  # any DB touch raises
            raise RuntimeError("db down")

    service = WalletService(test_session, cache=None)
    service.db = ExplodingSession()
    assert await service.checkout_gate_allows(uuid4()) is True


@pytest.mark.asyncio
async def test_get_or_create_wallet_is_stable(test_session):
    tenant = await _mk_tenant(test_session)
    service = WalletService(test_session, cache=None)
    w1 = await service.get_or_create_wallet(tenant.id)
    w2 = await service.get_or_create_wallet(tenant.id)
    assert w1.id == w2.id
