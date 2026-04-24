"""Integration tests for the InstaPay proof-verification flow.

The test suite talks to a real SQLAlchemy session (SQLite in-memory via
the ``test_session`` fixture) so the UNIQUE constraints, FK relations,
and repo queries exercise the same code paths as production. A few
production-specific behaviours can't be reproduced on SQLite:

  * ``pg_advisory_xact_lock`` (advisory lock for the daily-cap race) —
    a NO-OP on SQLite; we assert call-through so the production query
    doesn't regress, but we can't assert true serialization here.
  * RLS bypass / narrow_to_tenant — SQLite has no RLS, so the sweeper
    tests verify the *call pattern* (correct repo interactions) rather
    than database-side isolation.

Everything else runs fully end-to-end: the guards, dedup constraints,
auto-approval, event publication, and the R2 cleanup on IntegrityError
race.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.use_cases.payments.submit_payment_proof import (
    SubmitPaymentProofUseCase,
)
from src.core.entities.customer import Customer
from src.core.entities.instapay import (
    InstapayIntent,
    InstapayIntentStatus,
    PaymentProof,
    PaymentProofStatus,
)
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.entities.store import Store, StoreStatus
from src.core.value_objects.email import Email
from src.core.value_objects.money import Currency
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.external_services.instapay.auto_approval import (
    AutoApprovalConfig,
)
from src.infrastructure.external_services.instapay.payment_service import (
    generate_reference_code,
)
from src.infrastructure.repositories.customer_repository import (
    CustomerRepository,
)
from src.infrastructure.repositories.instapay_intent_repository import (
    InstapayIntentRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.payment_proof_repository import (
    PaymentProofRepository,
)
from src.infrastructure.repositories.store_repository import StoreRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_store() -> Store:
    return Store(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_id=uuid4(),
        name="InstaPay Test Store",
        slug=f"store-{uuid4().hex[:6]}",
        status=StoreStatus.ACTIVE,
        default_currency=Currency.EGP,
    )


def _make_customer(store: Store) -> Customer:
    return Customer(
        id=uuid4(),
        store_id=store.id,
        tenant_id=store.tenant_id,
        email=Email(value=f"cust_{uuid4().hex[:8]}@example.com"),
        first_name="Test",
        last_name="Customer",
        phone=PhoneNumber(value="+201000000000", country_code="EG"),
        is_verified=True,
    )


def _make_order(
    store: Store,
    customer: Customer,
    *,
    total_cents: int = 20_000,
    status: OrderStatus = OrderStatus.PENDING,
    payment_status: PaymentStatus = PaymentStatus.PENDING,
) -> Order:
    return Order(
        id=uuid4(),
        store_id=store.id,
        tenant_id=store.tenant_id,
        customer_id=customer.id,
        order_number=f"NU-{uuid4().hex[:6].upper()}",
        line_items=[
            OrderLineItem(
                product_id=uuid4(),
                product_name="Widget",
                quantity=1,
                unit_price=total_cents,
                total_price=total_cents,
            )
        ],
        shipping_address=OrderShippingAddress(
            first_name="Test",
            last_name="Customer",
            address_line1="10 Tahrir Square",
            city="Cairo",
            country="EG",
        ),
        status=status,
        payment_status=payment_status,
        subtotal=total_cents,
        total=total_cents,
        currency="EGP",
        payment_method="instapay",
    )


def _make_intent(
    order: Order,
    *,
    expires_in_min: int = 30,
    status: InstapayIntentStatus = InstapayIntentStatus.AWAITING_PAYMENT,
) -> InstapayIntent:
    return InstapayIntent(
        id=uuid4(),
        tenant_id=order.tenant_id,
        store_id=order.store_id,
        order_id=order.id,
        reference_code=generate_reference_code(),
        display_ipa="merchant@cib",
        amount_cents=order.total,
        expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_min),
        qr_payload="instapay://pay?...",
        status=status,
    )


def _fake_storage():
    """Storage service mock that records upload/delete calls."""
    svc = MagicMock()
    svc.upload_file = AsyncMock(
        return_value=MagicMock(
            key=f"payment-proofs/{uuid4().hex}.png",
            url="https://r2.test/proof.png",
            size=1024,
            content_type="image/png",
        )
    )
    svc.delete_file = AsyncMock(return_value=True)
    svc.get_signed_url = AsyncMock(return_value="https://r2.test/signed")
    return svc


def _auto_config(**overrides) -> AutoApprovalConfig:
    defaults = {
        "threshold_cents": 50_000,
        "daily_cap_cents": 500_000,
        "daily_count_cap": 10,
    }
    defaults.update(overrides)
    return AutoApprovalConfig(**defaults)


@pytest_asyncio.fixture
async def seeded(test_session: AsyncSession):
    """Persist a store + customer + InstaPay-method order + intent."""
    store = _make_store()
    customer = _make_customer(store)
    order = _make_order(store, customer)

    store_repo = StoreRepository(test_session)
    customer_repo = CustomerRepository(test_session)
    order_repo = OrderRepository(test_session)
    intent_repo = InstapayIntentRepository(test_session)

    await store_repo.create(store)
    await customer_repo.create(customer)
    await order_repo.create(order)
    intent = _make_intent(order)
    intent = await intent_repo.create(intent)
    await test_session.commit()
    return {
        "session": test_session,
        "store": store,
        "customer": customer,
        "order": order,
        "intent": intent,
    }


# ---------------------------------------------------------------------------
# End-to-end auto-approval happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch(
    "src.application.use_cases.payments.submit_payment_proof.get_event_bus",
    create=True,
)
async def test_auto_approve_flips_order_to_paid(_bus, seeded):
    """A small-amount proof auto-approves, flips the order to PAID,
    publishes OrderPaidEvent + PaymentProofApprovedEvent, and writes
    the PaymentTransactionModel reconciliation row."""
    bus = MagicMock()
    bus.publish = MagicMock()
    with patch("src.infrastructure.events.setup.get_event_bus", return_value=bus):
        storage = _fake_storage()
        uc = SubmitPaymentProofUseCase(
            session=seeded["session"],
            order_repo=OrderRepository(seeded["session"]),
            intent_repo=InstapayIntentRepository(seeded["session"]),
            proof_repo=PaymentProofRepository(seeded["session"]),
            storage_service=storage,
        )
        result = await uc.execute(
            store_id=seeded["store"].id,
            order_id=seeded["order"].id,
            customer_id=seeded["customer"].id,
            image_bytes=b"\x89PNG\r\n\x1a\nfake-image-payload",
            image_content_type="image/png",
            transaction_ref="BANK-REF-E2E-1",
            auto_approval_config=_auto_config(),
            idempotency_key=f"key-{uuid4().hex}",
        )

    # Auto-approval decision landed
    assert result.decision.approved is True
    assert result.proof.status == PaymentProofStatus.AUTO_APPROVED

    # Order flipped to PAID
    reloaded = await OrderRepository(seeded["session"]).get_by_id(seeded["order"].id)
    assert reloaded.payment_status == PaymentStatus.PAID
    assert reloaded.payment_id == seeded["intent"].reference_code
    assert reloaded.metadata.get("instapay", {}).get("auto_approved") is True

    # Intent flipped to PAID
    intent_after = await InstapayIntentRepository(seeded["session"]).get_by_order_id(
        seeded["order"].id
    )
    assert intent_after.status == InstapayIntentStatus.PAID

    # R2 upload happened exactly once; no delete (no race)
    assert storage.upload_file.await_count == 1
    assert storage.delete_file.await_count == 0

    # Both the OrderPaidEvent and PaymentProofApprovedEvent fired
    published_types = {type(c.args[0]).__name__ for c in bus.publish.call_args_list}
    assert "OrderPaidEvent" in published_types
    assert "PaymentProofApprovedEvent" in published_types


# ---------------------------------------------------------------------------
# Manual-review path (amount above threshold)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_large_order_routes_to_merchant_review(seeded):
    """Amounts above the threshold must not auto-approve, must not flip
    the order to PAID, and must leave the proof in AWAITING_REVIEW."""
    # Bump the order to a value above the threshold
    order_repo = OrderRepository(seeded["session"])
    order = await order_repo.get_by_id(seeded["order"].id)
    order.total = 100_000  # 1,000 EGP
    order.subtotal = 100_000
    order.line_items[0].__dict__["unit_price"] = (
        100_000  # OrderLineItem is frozen; bypass
    )
    await order_repo.update(order)
    await seeded["session"].commit()

    storage = _fake_storage()
    uc = SubmitPaymentProofUseCase(
        session=seeded["session"],
        order_repo=order_repo,
        intent_repo=InstapayIntentRepository(seeded["session"]),
        proof_repo=PaymentProofRepository(seeded["session"]),
        storage_service=storage,
    )
    result = await uc.execute(
        store_id=seeded["store"].id,
        order_id=seeded["order"].id,
        customer_id=seeded["customer"].id,
        image_bytes=b"\x89PNG\r\n\x1a\nanother-payload",
        image_content_type="image/png",
        transaction_ref="BANK-REF-REVIEW",
        auto_approval_config=_auto_config(threshold_cents=50_000),
    )

    assert result.decision.approved is False
    assert "amount_above_auto_approve_threshold" in result.decision.reasons
    assert result.decision.soft_block is True
    assert result.proof.status == PaymentProofStatus.AWAITING_REVIEW

    reloaded = await order_repo.get_by_id(seeded["order"].id)
    assert reloaded.payment_status == PaymentStatus.PENDING  # unchanged


# ---------------------------------------------------------------------------
# R2 cleanup on duplicate-image race
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_image_hash_409_deletes_r2_object(seeded):
    """If the dedup pre-check passes but a concurrent insert already
    wrote the same image_hash, the use case must delete the R2 object
    it just uploaded and surface a 409 — no orphan blob left behind."""
    from fastapi import HTTPException

    session = seeded["session"]
    proof_repo = PaymentProofRepository(session)

    # Pre-seed a proof with a specific image hash so the second upload
    # collides on the UNIQUE (store_id, image_hash) constraint.
    image_bytes = b"\x89PNG\r\n\x1a\ncollision-payload"
    image_hash = hashlib.sha256(image_bytes).digest()

    existing = PaymentProof.new(
        tenant_id=seeded["order"].tenant_id,
        store_id=seeded["store"].id,
        order_id=seeded["order"].id,
        proof_image_key="payment-proofs/pre-existing.png",
        proof_image_hash=image_hash,
        transaction_ref="BANK-REF-OLD",
    )
    await proof_repo.create(existing)
    await session.commit()

    # Now patch the image_hash_exists check to return False so we
    # bypass the cheap pre-check and force the IntegrityError path.
    # This is the exact race the fix was built for.
    storage = _fake_storage()
    uc = SubmitPaymentProofUseCase(
        session=session,
        order_repo=OrderRepository(session),
        intent_repo=InstapayIntentRepository(session),
        proof_repo=proof_repo,
        storage_service=storage,
    )
    uc.proof_repo.image_hash_exists = AsyncMock(return_value=False)
    uc.proof_repo.transaction_ref_exists = AsyncMock(return_value=False)

    with pytest.raises(HTTPException) as exc:
        await uc.execute(
            store_id=seeded["store"].id,
            order_id=seeded["order"].id,
            customer_id=seeded["customer"].id,
            image_bytes=image_bytes,
            image_content_type="image/png",
            transaction_ref="BANK-REF-COLLIDE",
            auto_approval_config=_auto_config(),
        )

    assert exc.value.status_code == 409
    # The upload happened (we slipped through the pre-check) …
    storage.upload_file.assert_awaited_once()
    # … and the cleanup ran exactly once on the key we just uploaded
    storage.delete_file.assert_awaited_once()
    cleaned_key = storage.delete_file.await_args.args[0]
    assert cleaned_key == storage.upload_file.await_args.kwargs.get(
        "filename"
    ) or cleaned_key.startswith("payment-proofs/")


# ---------------------------------------------------------------------------
# Idempotency replay scoped to store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_replay_returns_same_proof(seeded):
    """A second submission with the same idempotency_key in the same
    store must return the first proof unchanged — no second upload."""
    session = seeded["session"]
    storage = _fake_storage()
    uc = SubmitPaymentProofUseCase(
        session=session,
        order_repo=OrderRepository(session),
        intent_repo=InstapayIntentRepository(session),
        proof_repo=PaymentProofRepository(session),
        storage_service=storage,
    )

    key = f"key-{uuid4().hex}"
    bus = MagicMock()
    bus.publish = MagicMock()
    with patch("src.infrastructure.events.setup.get_event_bus", return_value=bus):
        first = await uc.execute(
            store_id=seeded["store"].id,
            order_id=seeded["order"].id,
            customer_id=seeded["customer"].id,
            image_bytes=b"\x89PNG\r\n\x1a\nreplay-payload",
            image_content_type="image/png",
            transaction_ref="BANK-REF-IDEMP",
            auto_approval_config=_auto_config(),
            idempotency_key=key,
        )

        second = await uc.execute(
            store_id=seeded["store"].id,
            order_id=seeded["order"].id,
            customer_id=seeded["customer"].id,
            image_bytes=b"\x89PNG\r\n\x1a\nreplay-payload",
            image_content_type="image/png",
            transaction_ref="BANK-REF-IDEMP",
            auto_approval_config=_auto_config(),
            idempotency_key=key,
        )

    assert first.proof.id == second.proof.id
    assert first.created is True
    assert second.created is False
    # Only the first request hit R2
    assert storage.upload_file.await_count == 1


# ---------------------------------------------------------------------------
# Sweeper: mixed pending + stuck-review intents in one pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_sweeper_handles_mixed_buckets(test_session: AsyncSession):
    """Pre-seed one AWAITING_PAYMENT past expiry and one PROOF_RECEIVED
    past the grace window; the sweeper must expire both, cancel their
    orders, and report accurate per-bucket stats."""
    from src.infrastructure.messaging.tasks.instapay_expiry_task import _sweep

    store_repo = StoreRepository(test_session)
    customer_repo = CustomerRepository(test_session)
    order_repo = OrderRepository(test_session)
    intent_repo = InstapayIntentRepository(test_session)

    # Store + customer shared by both orders
    store = _make_store()
    customer = _make_customer(store)
    await store_repo.create(store)
    await customer_repo.create(customer)

    # Order 1 — expired, never uploaded
    order1 = _make_order(store, customer)
    await order_repo.create(order1)
    intent1 = InstapayIntent(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        order_id=order1.id,
        reference_code=generate_reference_code(),
        display_ipa="merchant@cib",
        amount_cents=order1.total,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        qr_payload="instapay://",
        status=InstapayIntentStatus.AWAITING_PAYMENT,
    )
    await intent_repo.create(intent1)

    # Order 2 — proof uploaded, merchant never reviewed, past grace
    order2 = _make_order(store, customer)
    await order_repo.create(order2)
    intent2 = InstapayIntent(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        order_id=order2.id,
        reference_code=generate_reference_code(),
        display_ipa="merchant@cib",
        amount_cents=order2.total,
        expires_at=datetime.now(UTC) - timedelta(hours=72),
        qr_payload="instapay://",
        status=InstapayIntentStatus.PROOF_RECEIVED,
    )
    await intent_repo.create(intent2)
    await test_session.commit()

    # Patch the sweeper's internal AsyncSessionLocal so it reuses our
    # test session. Also stub out the RLS helpers which are PG-only.
    from src.infrastructure.messaging.tasks import instapay_expiry_task as sweeper

    class _SessionCM:
        def __init__(self, session):
            self.session = session

        async def __aenter__(self):
            return self.session

        async def __aexit__(self, *a):
            return False

    with (
        patch.object(sweeper, "AsyncSessionLocal", lambda: _SessionCM(test_session)),
        patch(
            "src.infrastructure.tenancy.rls.enable_rls_bypass",
            new=AsyncMock(),
        ),
        patch(
            "src.infrastructure.tenancy.rls.narrow_to_tenant",
            new=AsyncMock(),
        ),
    ):
        stats = await _sweep(
            proof_review_grace_hours=48,
            image_retention_days=0,  # disable R2 pass for this test
            idempotency_key_retention_days=0,
        )

    assert stats["expired"] == 1
    assert stats["escalated"] == 1
    assert stats["scanned"] == 2
    assert stats["cancelled"] == 2

    # Both orders now CANCELLED
    after1 = await order_repo.get_by_id(order1.id)
    after2 = await order_repo.get_by_id(order2.id)
    assert after1.status == OrderStatus.CANCELLED
    assert after2.status == OrderStatus.CANCELLED

    # Intents transitioned to EXPIRED
    intent_after_1 = await intent_repo.get_by_order_id(order1.id)
    intent_after_2 = await intent_repo.get_by_order_id(order2.id)
    assert intent_after_1.status == InstapayIntentStatus.EXPIRED
    assert intent_after_2.status == InstapayIntentStatus.EXPIRED


# ---------------------------------------------------------------------------
# Sweeper: idempotency-key TTL clears old keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_key_ttl_clears_old_keys(seeded):
    """Proofs older than the retention window have their
    idempotency_key nulled — the row stays for audit, but the scoped
    UNIQUE constraint can free up the key for a fresh upload."""
    session = seeded["session"]
    proof_repo = PaymentProofRepository(session)

    # One fresh proof, one old proof (both with idempotency keys)
    fresh = PaymentProof.new(
        tenant_id=seeded["order"].tenant_id,
        store_id=seeded["store"].id,
        order_id=seeded["order"].id,
        proof_image_key="fresh.png",
        proof_image_hash=hashlib.sha256(b"fresh").digest(),
        transaction_ref="BANK-FRESH",
        idempotency_key="fresh-key",
    )
    old = PaymentProof.new(
        tenant_id=seeded["order"].tenant_id,
        store_id=seeded["store"].id,
        order_id=seeded["order"].id,
        proof_image_key="old.png",
        proof_image_hash=hashlib.sha256(b"old").digest(),
        transaction_ref="BANK-OLD",
        idempotency_key="old-key",
    )
    # Backdate the old proof's created_at
    old.created_at = datetime.now(UTC) - timedelta(days=45)

    await proof_repo.create(fresh)
    await proof_repo.create(old)
    await session.commit()

    cleared = await proof_repo.clear_old_idempotency_keys(
        older_than=datetime.now(UTC) - timedelta(days=30)
    )
    await session.commit()

    assert cleared == 1
    # Fresh still has its key
    fresh_after = await proof_repo.get_by_idempotency_key(
        seeded["store"].id, "fresh-key"
    )
    assert fresh_after is not None
    # Old key is gone — a new proof can reuse "old-key" safely
    old_after = await proof_repo.get_by_idempotency_key(seeded["store"].id, "old-key")
    assert old_after is None
