"""Unit tests — admin wallet settings (platform_config) + on-hold credits."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.services.wallet_service import WalletService
from src.application.services.wallet_settings import (
    get_wallet_settings,
    invalidate_wallet_settings_cache,
    resolve_commission_bps,
    update_wallet_settings,
)
from src.application.use_cases.wallet.review_topup_proof import (
    ReviewTopupProofUseCase,
)
from src.core.entities.wallet import TopupIntentStatus
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.wallet import (
    MerchantWalletModel,
    WalletTopupIntentModel,
    WalletTopupProofModel,
    WalletTransactionModel,
)


@pytest.fixture(autouse=True)
def _fresh_wallet_settings():
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


async def _mk_under_review_topup(
    session, tenant_id, amount_cents=10_000
) -> tuple[WalletTopupIntentModel, WalletTopupProofModel]:
    """Seed the state submit_topup_proof leaves after a soft-block:
    intent under_review + proof awaiting_review + pending hold."""
    intent = WalletTopupIntentModel(
        tenant_id=tenant_id,
        method="vodafone_cash",
        amount_cents=amount_cents,
        currency="EGP",
        status=TopupIntentStatus.UNDER_REVIEW.value,
        special_reference=f"VC-{uuid4().hex[:6].upper()}",
        display_destination="01000000000",
    )
    session.add(intent)
    await session.flush()
    proof = WalletTopupProofModel(
        tenant_id=tenant_id,
        topup_intent_id=intent.id,
        proof_image_key=f"wallet-topups/{tenant_id}/x.bin",
        proof_image_hash=uuid4().bytes,
        transaction_ref=f"tx-{uuid4().hex[:10]}",
        status="awaiting_review",
    )
    session.add(proof)

    service = WalletService(session)
    wallet = await service.get_or_create_wallet(tenant_id)
    service.add_pending_hold(wallet, amount_cents)
    await session.commit()
    return intent, proof


# ─── Admin settings ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_default_from_env_then_admin_override(test_session):
    admin = await get_wallet_settings(test_session, use_cache=False)
    assert admin.commission_bps_default is None
    assert admin.card_enabled is True

    merged = await update_wallet_settings(
        test_session,
        {
            "card_enabled": False,
            "commission_bps_default": 250,
            "vodafone_cash_number": "01012345678",
            "not_a_real_field": "ignored",
        },
    )
    await test_session.commit()
    assert merged.card_enabled is False
    assert merged.commission_bps_default == 250
    assert merged.vodafone_cash_number == "01012345678"
    assert merged.method_enabled("card") is False
    assert merged.method_enabled("vodafone_cash") is True

    # None clears an override back to the env default.
    merged = await update_wallet_settings(test_session, {"card_enabled": None})
    await test_session.commit()
    assert merged.card_enabled is True


def test_resolve_commission_bps_precedence():
    from src.application.services.wallet_settings import WalletAdminSettings

    def mk(default):
        return WalletAdminSettings(
            topups_enabled=True,
            checkout_gate_enabled=False,
            golive_gate_enabled=False,
            card_enabled=True,
            vodafone_cash_enabled=True,
            instapay_enabled=True,
            commission_bps_default=default,
            negative_allowance_cents=5_000,
            low_balance_threshold_cents=10_000,
            min_topup_cents=5_000,
            vodafone_cash_number=None,
            instapay_ipa=None,
            instapay_display_name=None,
        )

    # Tenant override always wins.
    assert resolve_commission_bps(300, 150, mk(250)) == 150
    # Admin default tunes commission-bearing plans...
    assert resolve_commission_bps(300, None, mk(250)) == 250
    # ...but can never start charging subscription tenants (plan bps 0).
    assert resolve_commission_bps(0, None, mk(250)) == 0
    # No admin default -> plan rate.
    assert resolve_commission_bps(300, None, mk(None)) == 300


@pytest.mark.asyncio
async def test_admin_commission_rate_applies_to_payg(test_session):
    tenant = await _mk_tenant(test_session, plan="payg")
    service = WalletService(test_session, cache=None)
    wallet = await service.get_or_create_wallet(tenant.id)
    await test_session.commit()

    assert await service.effective_commission_bps_admin(tenant, wallet) == 300

    await update_wallet_settings(test_session, {"commission_bps_default": 200})
    await test_session.commit()
    assert await service.effective_commission_bps_admin(tenant, wallet) == 200

    # Subscription tenants stay at zero regardless of the admin default.
    tenant.plan = "starter"
    assert await service.effective_commission_bps_admin(tenant, None) == 0


# ─── Go-live gate + rate lock at signup ──────────────────────────────────


@pytest.mark.asyncio
async def test_golive_gate_blocks_new_trial_tenants_only(test_session, monkeypatch):
    from src.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "ff_golive_gate", True)
    service = WalletService(test_session, cache=None)
    # `cache=None` still attaches Redis when redis_host is configured, and the
    # gate answer is cached for 60s — which would serve a stale "not_live"
    # after the tenant's plan changes below. Read the live state instead.
    service._cache = None

    # New trial tenant (no golive_exempt flag) -> not live.
    gated = await _mk_tenant(test_session, plan="trial")
    assert await service.checkout_gate_state(gated.id) == "not_live"

    # Grandfathered trial tenant (backfilled flag) -> unaffected.
    exempt = await _mk_tenant(test_session, plan="trial")
    exempt.feature_flags = {"golive_exempt": True}
    await test_session.commit()
    assert await service.checkout_gate_state(exempt.id) == "ok"

    # Choosing Pay as you Grow opens the gate.
    gated.plan = "payg"
    await test_session.commit()
    assert await service.checkout_gate_state(gated.id) == "ok"

    # Gate off -> nobody is blocked. (Drop the 60s settings cache the
    # way production does when the admin flips the switch.)
    monkeypatch.setattr(get_settings(), "ff_golive_gate", False)
    invalidate_wallet_settings_cache()
    fresh = await _mk_tenant(test_session, plan="trial")
    assert await service.checkout_gate_state(fresh.id) == "ok"


@pytest.mark.asyncio
async def test_payg_activation_locks_commission_rate(test_session):
    from src.application.use_cases.billing.subscribe import SubscribeUseCase

    # Admin default at signup time is 250 bps.
    await update_wallet_settings(test_session, {"commission_bps_default": 250})
    await test_session.commit()

    tenant = await _mk_tenant(test_session, plan="trial")
    tenant.lifecycle_state = "trial"
    await test_session.commit()

    await SubscribeUseCase(test_session).execute(tenant_id=tenant.id, plan="payg")
    await test_session.commit()

    service = WalletService(test_session, cache=None)
    wallet = await service.get_or_create_wallet(tenant.id)
    assert wallet.commission_bps_override == 250  # locked at signup

    # Admin later raises the default -> existing merchant keeps 250.
    await update_wallet_settings(test_session, {"commission_bps_default": 400})
    await test_session.commit()
    assert await service.effective_commission_bps_admin(tenant, wallet) == 250

    # ...while a brand-new signup gets the new rate.
    newcomer = await _mk_tenant(test_session, plan="trial")
    await SubscribeUseCase(test_session).execute(tenant_id=newcomer.id, plan="payg")
    await test_session.commit()
    new_wallet = await service.get_or_create_wallet(newcomer.id)
    assert new_wallet.commission_bps_override == 400


# ─── On-hold (pending) credits ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_pending_hold_add_and_release():
    wallet = MerchantWalletModel(
        tenant_id=uuid4(), balance_cents=0, pending_balance_cents=0
    )
    WalletService.add_pending_hold(wallet, 10_000)
    assert wallet.pending_balance_cents == 10_000
    WalletService.release_pending_hold(wallet, 10_000)
    assert wallet.pending_balance_cents == 0
    # Never goes negative even on a double release.
    WalletService.release_pending_hold(wallet, 10_000)
    assert wallet.pending_balance_cents == 0


@pytest.mark.asyncio
async def test_admin_approve_converts_hold_to_real_credit(test_session):
    tenant = await _mk_tenant(test_session)
    intent, proof = await _mk_under_review_topup(test_session, tenant.id)

    result = await ReviewTopupProofUseCase(test_session).approve(
        proof_id=proof.id, admin_user_id=None
    )
    await test_session.commit()

    wallet = await WalletService(test_session).get_or_create_wallet(tenant.id)
    assert wallet.pending_balance_cents == 0  # hold released
    assert wallet.balance_cents == 10_000  # real ledger credit
    assert result.credited_balance_cents == 10_000
    assert intent.status == TopupIntentStatus.SUCCEEDED.value

    from sqlalchemy import select

    tx = (
        await test_session.execute(
            select(WalletTransactionModel).where(
                WalletTransactionModel.tenant_id == tenant.id
            )
        )
    ).scalar_one()
    assert tx.kind == "topup"
    assert tx.idempotency_key == f"proof:{proof.id}"


@pytest.mark.asyncio
async def test_admin_reject_drops_hold_without_credit(test_session):
    tenant = await _mk_tenant(test_session)
    intent, proof = await _mk_under_review_topup(test_session, tenant.id)

    await ReviewTopupProofUseCase(test_session).reject(
        proof_id=proof.id, admin_user_id=None, reason="Amount not received"
    )
    await test_session.commit()

    wallet = await WalletService(test_session).get_or_create_wallet(tenant.id)
    assert wallet.pending_balance_cents == 0  # hold gone
    assert wallet.balance_cents == 0  # nothing credited
    assert intent.status == TopupIntentStatus.AWAITING_PROOF.value  # retryable

    from sqlalchemy import select

    txs = (
        (
            await test_session.execute(
                select(WalletTransactionModel).where(
                    WalletTransactionModel.tenant_id == tenant.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert txs == []


# ─── Method configuration gating + admin min top-up ──────────────────────


@pytest.mark.asyncio
async def test_effective_methods_require_configuration(test_session):
    """An enabled method with no platform destination must NOT be offered
    to merchants (the hub dialog reads effective_methods_map)."""
    admin = await get_wallet_settings(test_session, use_cache=False)
    # Test env has no VC number / InstaPay IPA configured.
    assert admin.vodafone_cash_enabled is True
    assert admin.effective_methods_map()["vodafone_cash"] is False
    assert admin.effective_methods_map()["instapay"] is False

    merged = await update_wallet_settings(
        test_session, {"vodafone_cash_number": "01012345678"}
    )
    await test_session.commit()
    assert merged.effective_methods_map()["vodafone_cash"] is True
    # Toggle off wins over configured.
    merged = await update_wallet_settings(
        test_session, {"vodafone_cash_enabled": False}
    )
    await test_session.commit()
    assert merged.effective_methods_map()["vodafone_cash"] is False


@pytest.mark.asyncio
async def test_admin_min_topup_enforced(test_session):
    """CreateTopupUseCase rejects amounts below the admin-set minimum."""
    from fastapi import HTTPException

    from src.application.use_cases.wallet.create_topup import CreateTopupUseCase
    from src.core.entities.wallet import TopupMethod

    tenant = await _mk_tenant(test_session)
    await update_wallet_settings(
        test_session,
        {
            "topups_enabled": True,
            "instapay_enabled": True,
            "instapay_ipa": "numu@instapay",
            "min_topup_cents": 10_000,
        },
    )
    await test_session.commit()
    invalidate_wallet_settings_cache()

    with pytest.raises(HTTPException) as exc:
        await CreateTopupUseCase(test_session).execute(
            tenant_id=tenant.id,
            user_id=uuid4(),
            method=TopupMethod.INSTAPAY,
            amount_cents=6_000,
        )
    assert exc.value.status_code == 422
    assert "100" in exc.value.detail  # dynamic min (100 EGP) in the message

    # At the minimum it goes through.
    result = await CreateTopupUseCase(test_session).execute(
        tenant_id=tenant.id,
        user_id=uuid4(),
        method=TopupMethod.INSTAPAY,
        amount_cents=10_000,
    )
    await test_session.commit()
    assert result.intent.amount_cents == 10_000


# ─── Perceptual hash: unsigned 64-bit → signed BIGINT ────────────────────


@pytest.mark.asyncio
async def test_topup_proof_phash_top_bit_set_persists(test_session):
    """Real receipt images produce unsigned 64-bit hashes; any hash with
    the top bit set (~half of them) overflows a signed BIGINT unless
    mapped through phash_to_db. The raw value 500'd on prod (asyncpg
    'value out of int64 range')."""
    from src.infrastructure.repositories.payment_proof_repository import (
        phash_from_db,
        phash_to_db,
    )

    raw = 12076796887427370150  # the exact prod-crashing value
    assert raw > (1 << 63) - 1
    stored = phash_to_db(raw)
    assert stored is not None and stored < 0
    assert phash_from_db(stored) == raw
    assert phash_to_db(None) is None

    tenant = await _mk_tenant(test_session)
    intent = WalletTopupIntentModel(
        tenant_id=tenant.id,
        method="vodafone_cash",
        amount_cents=1_000,
        currency="EGP",
        status=TopupIntentStatus.AWAITING_PROOF.value,
        special_reference=f"VC-{uuid4().hex[:6].upper()}",
        display_destination="01000000000",
    )
    test_session.add(intent)
    await test_session.flush()
    proof = WalletTopupProofModel(
        tenant_id=tenant.id,
        topup_intent_id=intent.id,
        proof_image_key=f"wallet-topups/{tenant.id}/x.bin",
        proof_image_hash=uuid4().bytes,
        perceptual_hash=phash_to_db(raw),
        transaction_ref=f"tx-{uuid4().hex[:10]}",
        status="awaiting_review",
    )
    test_session.add(proof)
    await test_session.commit()
    assert phash_from_db(proof.perceptual_hash) == raw


# ─── No blind auto-approval without OCR verification ─────────────────────


class _FakeStorage:
    async def upload_file(self, *, file_content, filename, content_type, bucket):
        from types import SimpleNamespace

        return SimpleNamespace(key=filename)

    async def delete_file(self, key):
        return True


@pytest.mark.asyncio
async def test_no_ocr_never_auto_approves(test_session):
    """With no OCR provider the engine's cross-checks no-op, so a fake
    image + wrong ref below the threshold used to auto-approve on pure
    trust (seen live 2026-07-19). Unverified receipts must land ON HOLD
    for admin review instead — never as spendable balance."""
    from datetime import UTC, datetime, timedelta

    from src.application.use_cases.wallet.submit_topup_proof import (
        SubmitTopupProofUseCase,
    )
    from src.infrastructure.external_services.vision import NoopProofVisionService

    tenant = await _mk_tenant(test_session)
    intent = WalletTopupIntentModel(
        tenant_id=tenant.id,
        method="vodafone_cash",
        amount_cents=1_000,  # far below the auto-approve threshold
        currency="EGP",
        status=TopupIntentStatus.AWAITING_PROOF.value,
        special_reference=f"VC-{uuid4().hex[:6].upper()}",
        display_destination="01000000000",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    test_session.add(intent)
    await test_session.commit()

    result = await SubmitTopupProofUseCase(
        session=test_session,
        storage_service=_FakeStorage(),
        vision_service=NoopProofVisionService(),
    ).execute(
        tenant_id=tenant.id,
        topup_intent_id=intent.id,
        image_bytes=b"definitely-not-a-real-receipt",
        image_content_type="image/jpeg",
        transaction_ref=f"fake-{uuid4().hex[:8]}",
    )
    await test_session.commit()

    assert result.credited_balance_cents is None
    assert result.on_hold is True
    assert result.proof.status == "awaiting_review"
    assert "ocr_verification_unavailable" in result.decision.reasons
    assert result.intent.status == TopupIntentStatus.UNDER_REVIEW.value

    # The merchant sees the amount as pending, not spendable.
    wallet = await WalletService(test_session).get_or_create_wallet(tenant.id)
    assert wallet.pending_balance_cents == 1_000
    assert wallet.balance_cents == 0


@pytest.mark.asyncio
async def test_ocr_text_without_amount_gets_specific_reason(test_session):
    """OCR that reads text but finds NO payment amount (screenshot of a
    non-receipt) must block with ocr_no_amount_found — not the
    misleading 'unavailable' (OCR ran fine, seen live 2026-07-19)."""
    from datetime import UTC, datetime, timedelta

    from src.application.use_cases.wallet.submit_topup_proof import (
        SubmitTopupProofUseCase,
    )
    from src.infrastructure.external_services.vision import ProofVisionResult

    class _OkNoAmountVision:
        async def extract(self, image_bytes, *, hint_currency="EGP"):
            return ProofVisionResult(
                status="ok",
                provider="google_vision",
                raw_text="an architecture diagram with lots of words but no money",
            )

    tenant = await _mk_tenant(test_session)
    intent = WalletTopupIntentModel(
        tenant_id=tenant.id,
        method="vodafone_cash",
        amount_cents=25_000,
        currency="EGP",
        status=TopupIntentStatus.AWAITING_PROOF.value,
        special_reference=f"VC-{uuid4().hex[:6].upper()}",
        display_destination="01000000000",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    test_session.add(intent)
    await test_session.commit()

    result = await SubmitTopupProofUseCase(
        session=test_session,
        storage_service=_FakeStorage(),
        vision_service=_OkNoAmountVision(),
    ).execute(
        tenant_id=tenant.id,
        topup_intent_id=intent.id,
        image_bytes=b"screenshot-of-a-diagram",
        image_content_type="image/png",
        transaction_ref=f"ref-{uuid4().hex[:8]}",
    )
    await test_session.commit()

    assert result.credited_balance_cents is None
    assert result.on_hold is True
    assert "ocr_no_amount_found" in result.decision.reasons
    assert "ocr_verification_unavailable" not in result.decision.reasons
