"""Merchant-recorded part payments on the order page.

The merchant attaches a Vodafone Cash / InstaPay receipt and types how much
was actually paid; the order shows "paid X, remaining Y" until the running
total settles it. The amount is the merchant's — nothing reads the image.

Two things are worth a test here and both are money:

  * the arithmetic (partial, instalments, overpay, void) and the single
    point where the order flips to PAID, and
  * the coexistence guarantees with the customer-facing proof + OCR
    pipeline, which writes to the same table: a recorded payment must be
    APPROVED (never AUTO_APPROVED, which would eat the store's daily
    auto-approval budget) and must never let the expiry sweeper cancel an
    order that already has money on it.

Route handlers are called directly with fakes, matching
``test_deposit_policy_persistence.py`` — no Postgres needed. The two SQL
queries that fakes cannot vouch for are exercised against the in-memory
session fixture at the bottom.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image

from src.api.v1.routes.stores import payment_proofs as module
from src.api.v1.routes.stores.payment_proofs import (
    VoidPaymentRequest,
    record_order_payment,
    void_recorded_payment,
)
from src.core.entities.instapay import PaymentProofStatus
from src.core.entities.order import OrderStatus, PaymentStatus

STORE_ID = uuid4()
TENANT_ID = uuid4()
USER_ID = uuid4()


# ── Fakes ────────────────────────────────────────────────────────────


def _png(colour: tuple[int, int, int]) -> bytes:
    """A real 32x32 PNG — the upload validator sniffs magic bytes and the
    sanitizer decodes with PIL, so a dummy byte string will not do."""
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), colour).save(buf, format="PNG")
    return buf.getvalue()


class _Upload:
    """The slice of ``UploadFile`` the two helpers actually touch."""

    def __init__(self, content: bytes, content_type: str = "image/png") -> None:
        self._content = content
        self.content_type = content_type
        self.filename = "receipt.png"

    async def read(self) -> bytes:
        return self._content


class _FakeStorage:
    def __init__(self) -> None:
        self.uploaded: list[str] = []
        self.deleted: list[str] = []

    async def upload_file(self, *, file_content, filename, content_type, bucket):
        self.uploaded.append(filename)
        return SimpleNamespace(key=f"payment-proofs/{filename}")

    async def delete_file(self, key):
        self.deleted.append(key)


class _FakeProofRepo:
    """In-memory stand-in mirroring the real repository's contracts."""

    def __init__(self) -> None:
        self.rows: list = []

    async def get_by_idempotency_key(self, store_id, idempotency_key):
        for p in self.rows:
            if p.store_id == store_id and p.idempotency_key == idempotency_key:
                return p
        return None

    async def amount_paid_cents(self, order_id) -> int:
        return sum(
            p.declared_amount_cents or 0
            for p in self.rows
            if p.order_id == order_id
            and p.status
            in (PaymentProofStatus.APPROVED, PaymentProofStatus.AUTO_APPROVED)
        )

    async def image_hash_exists(self, store_id, image_hash) -> bool:
        # Mirrors the real query, including the exemption for voided
        # merchant-recorded rows. Getting this wrong would make the
        # re-record test pass against a fake kinder than the database.
        return any(
            p.store_id == store_id
            and p.proof_image_hash == image_hash
            and not (
                p.recorded_method is not None
                and p.status is PaymentProofStatus.REJECTED
            )
            for p in self.rows
        )

    async def transaction_ref_exists(self, store_id, transaction_ref) -> bool:
        # Mirrors the real query, including the exemption for voided
        # merchant-recorded rows — same reason as image_hash_exists.
        return any(
            p.store_id == store_id
            and p.transaction_ref == transaction_ref
            and not (
                p.recorded_method is not None
                and p.status is PaymentProofStatus.REJECTED
            )
            for p in self.rows
        )

    async def create(self, proof):
        self.rows.append(proof)
        return proof

    async def get_by_id(self, proof_id):
        return next((p for p in self.rows if p.id == proof_id), None)

    async def update(self, proof):
        return proof


class _FakeOrderRepo:
    def __init__(self, order) -> None:
        self.order = order

    async def get_by_id(self, order_id):
        return self.order if self.order.id == order_id else None

    async def update(self, order):
        return order


class _FakeActivityRepo:
    def __init__(self) -> None:
        self.created: list = []

    async def create(self, activity):
        self.created.append(activity)
        return activity


class _FakeIntentRepo:
    def __init__(self, session=None) -> None:
        self.updates: list = []

    async def get_by_order_id(self, order_id):
        return None

    async def update_status(self, intent_id, status):
        self.updates.append((intent_id, status))


def _order(total: int = 35000, **overrides) -> SimpleNamespace:
    base = {
        "id": uuid4(),
        "store_id": STORE_ID,
        "tenant_id": TENANT_ID,
        "order_number": "NUMU-1001",
        "total": total,
        "collectible_total": total,
        "currency": "EGP",
        "payment_status": PaymentStatus.PENDING,
        "status": OrderStatus.PENDING,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def wiring(monkeypatch):
    """Patch the two repositories the route constructs internally."""
    proof_repo = _FakeProofRepo()
    intent_repo = _FakeIntentRepo()
    monkeypatch.setattr(module, "PaymentProofRepository", lambda _db: proof_repo)
    monkeypatch.setattr(
        module, "ManualPaymentIntentRepository", lambda _db: intent_repo
    )
    return SimpleNamespace(proofs=proof_repo, intents=intent_repo)


@pytest.fixture
def paid_calls(monkeypatch):
    """Capture every flip to PAID so we can assert it happens exactly once."""
    from src.api.v1.routes.stores import orders as orders_module

    calls: list = []

    async def _apply(order, order_repo):
        calls.append(order.id)
        order.payment_status = PaymentStatus.PAID
        return order

    monkeypatch.setattr(orders_module, "apply_manual_payment", _apply)
    return calls


async def _record(order, *, amount, method="vodafone_cash", image=None, **kwargs):
    return await record_order_payment(
        store=SimpleNamespace(id=STORE_ID),
        order_id=order.id,
        user_id=USER_ID,
        db=object(),
        storage_service=kwargs.pop("storage", _FakeStorage()),
        order_repo=kwargs.pop("order_repo", _FakeOrderRepo(order)),
        activity_repo=kwargs.pop("activity_repo", _FakeActivityRepo()),
        image=_Upload(image if image is not None else _png((10, 20, 30))),
        amount_cents=amount,
        method=method,
        reference=kwargs.pop("reference", None),
        idempotency_key=kwargs.pop("idempotency_key", None),
    )


# ── The arithmetic ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_part_payment_leaves_the_balance_outstanding(wiring, paid_calls):
    """100 recorded on a 350 order: still pending, 250 to go."""
    order = _order(total=35000)

    result = await _record(order, amount=10000)

    assert result.data.amount_paid_cents == 10000
    assert result.data.balance_due_cents == 25000
    assert result.data.order_payment_status == PaymentStatus.PENDING.value
    assert paid_calls == []


@pytest.mark.asyncio
async def test_instalments_settle_the_order_exactly_once(wiring, paid_calls):
    """Second payment closes the balance and flips the order to paid."""
    order = _order(total=35000)

    first = await _record(order, amount=10000, image=_png((1, 1, 1)))
    assert first.data.balance_due_cents == 25000

    second = await _record(order, amount=25000, image=_png((2, 2, 2)))

    assert second.data.amount_paid_cents == 35000
    assert second.data.balance_due_cents == 0
    assert second.data.order_payment_status == PaymentStatus.PAID.value
    assert paid_calls == [order.id], "OrderPaidEvent path must fire once, not twice"


@pytest.mark.asyncio
async def test_overpayment_clamps_the_balance_at_zero(wiring, paid_calls):
    order = _order(total=35000)

    result = await _record(order, amount=40000)

    assert result.data.amount_paid_cents == 40000
    assert result.data.balance_due_cents == 0
    assert result.data.order_payment_status == PaymentStatus.PAID.value


@pytest.mark.asyncio
async def test_balance_follows_collected_total_after_partial_acceptance(
    wiring, paid_calls
):
    """A door-side partial acceptance lowers what the merchant collects."""
    order = _order(total=35000, collectible_total=20000)

    result = await _record(order, amount=20000)

    assert result.data.balance_due_cents == 0
    assert paid_calls == [order.id]


# ── Guards ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_method_is_rejected(wiring):
    with pytest.raises(HTTPException) as exc:
        await _record(_order(), amount=1000, method="bitcoin")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_already_paid_order_is_rejected(wiring):
    order = _order(payment_status=PaymentStatus.PAID)
    with pytest.raises(HTTPException) as exc:
        await _record(order, amount=1000)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_cancelled_order_is_rejected(wiring):
    order = _order(status=OrderStatus.CANCELLED)
    with pytest.raises(HTTPException) as exc:
        await _record(order, amount=1000)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_same_receipt_cannot_be_recorded_twice(wiring, paid_calls):
    """The same screenshot is the same money — the second one is a 409."""
    order = _order()
    same = _png((7, 7, 7))

    await _record(order, amount=5000, image=same)
    with pytest.raises(HTTPException) as exc:
        await _record(order, amount=5000, image=same)

    assert exc.value.status_code == 409
    assert len(wiring.proofs.rows) == 1


@pytest.mark.asyncio
async def test_transaction_reference_cannot_be_reused(wiring, paid_calls):
    order = _order()

    await _record(order, amount=5000, image=_png((3, 3, 3)), reference="VF-1234")
    with pytest.raises(HTTPException) as exc:
        await _record(order, amount=5000, image=_png((4, 4, 4)), reference="VF-1234")

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_idempotent_replay_does_not_record_the_money_twice(wiring, paid_calls):
    order = _order()

    first = await _record(
        order, amount=5000, image=_png((5, 5, 5)), idempotency_key="abc-123"
    )
    again = await _record(
        order, amount=5000, image=_png((6, 6, 6)), idempotency_key="abc-123"
    )

    assert len(wiring.proofs.rows) == 1
    assert again.data.payment.id == first.data.payment.id
    assert again.data.amount_paid_cents == 5000


@pytest.mark.asyncio
async def test_idempotent_replay_still_works_after_the_order_settled(
    wiring, paid_calls
):
    """The retry that arrives after the money landed must not 409.

    The first request settles the order. If the idempotency check sat behind
    the "already fully paid" guard, the retry a dropped connection provokes
    would look like a failure — and the merchant would record the money a
    second time by hand.
    """
    order = _order(total=10000)

    first = await _record(
        order, amount=10000, image=_png((9, 9, 9)), idempotency_key="settle-1"
    )
    assert order.payment_status is PaymentStatus.PAID

    again = await _record(
        order, amount=10000, image=_png((8, 8, 8)), idempotency_key="settle-1"
    )

    assert again.data.payment.id == first.data.payment.id
    assert again.data.order_payment_status == PaymentStatus.PAID.value
    assert len(wiring.proofs.rows) == 1
    assert paid_calls == [order.id], "the order must not be settled twice"


@pytest.mark.asyncio
async def test_idempotency_key_from_another_order_is_refused(wiring, paid_calls):
    """Keys are unique per store, not per order.

    Answering with the other order's totals would be worse than refusing —
    the caller asked about this order.
    """
    order_a = _order(total=35000)
    order_b = _order(total=35000)

    await _record(
        order_a, amount=5000, image=_png((11, 11, 11)), idempotency_key="shared"
    )

    with pytest.raises(HTTPException) as exc:
        await _record(
            order_b, amount=5000, image=_png((12, 12, 12)), idempotency_key="shared"
        )

    assert exc.value.status_code == 409
    assert len(wiring.proofs.rows) == 1


@pytest.mark.asyncio
async def test_missing_order_is_404_not_a_crash(wiring, paid_calls):
    """``order_repo.get_by_id`` returns None for a deleted order and for one
    outside the current tenant scope. Reaching the totals with that None was
    an AttributeError surfacing as a 500."""
    order = _order()
    missing = _order()

    with pytest.raises(HTTPException) as exc:
        await _record(missing, amount=5000, order_repo=_FakeOrderRepo(order))

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_missing_reference_gets_a_generated_one(wiring, paid_calls):
    order = _order()
    result = await _record(order, amount=5000)
    assert result.data.payment.transaction_ref.startswith("MAN-")


@pytest.mark.asyncio
async def test_undecodable_image_is_415(wiring):
    """Magic bytes say PNG, the payload is not an image."""
    with pytest.raises(HTTPException) as exc:
        await _record(_order(), amount=1000, image=b"\x89PNG\r\n\x1a\nnot-an-image")
    assert exc.value.status_code == 415


# ── Coexistence with the customer proof + OCR pipeline ───────────────


@pytest.mark.asyncio
async def test_recorded_payment_is_approved_not_auto_approved(wiring, paid_calls):
    """AUTO_APPROVED would consume the store's daily auto-approval budget.

    ``daily_auto_approve_stats`` filters on AUTO_APPROVED, so writing that
    status here would let a busy merchant exhaust the cap and silently
    soft-block a genuine customer proof later the same day.
    """
    await _record(_order(), amount=5000)

    row = wiring.proofs.rows[0]
    assert row.status is PaymentProofStatus.APPROVED
    assert row.review_decision_by == USER_ID


@pytest.mark.asyncio
async def test_no_ocr_columns_are_written(wiring, paid_calls):
    """Nothing reads the image on this path."""
    await _record(_order(), amount=5000)

    row = wiring.proofs.rows[0]
    assert row.ocr_status is None
    assert row.ocr_extracted_amount_cents is None
    assert row.ocr_provider is None
    assert row.auto_approval_block_reasons is None


@pytest.mark.asyncio
async def test_recorded_method_marks_the_row_as_merchant_recorded(wiring, paid_calls):
    await _record(_order(), amount=5000, method="instapay")
    assert wiring.proofs.rows[0].recorded_method == "instapay"


@pytest.mark.asyncio
async def test_timeline_entry_is_written(wiring, paid_calls):
    order = _order()
    activity_repo = _FakeActivityRepo()

    await _record(order, amount=5000, activity_repo=activity_repo)

    assert len(activity_repo.created) == 1
    entry = activity_repo.created[0]
    assert entry.event_type == "payment_recorded"
    assert entry.metadata["amount_cents"] == 5000


# ── Void ─────────────────────────────────────────────────────────────


async def _void(
    order, proof_id, *, proofs, reason="Wrong amount typed", order_repo=None
):
    return await void_recorded_payment(
        store=SimpleNamespace(id=STORE_ID),
        proof_id=proof_id,
        body=VoidPaymentRequest(reason=reason),
        user_id=USER_ID,
        db=object(),
        storage_service=_FakeStorage(),
        order_repo=order_repo or _FakeOrderRepo(order),
        activity_repo=_FakeActivityRepo(),
    )


@pytest.mark.asyncio
async def test_void_puts_the_amount_back_on_the_balance(wiring, paid_calls):
    order = _order(total=35000)
    recorded = await _record(order, amount=10000)
    assert recorded.data.balance_due_cents == 25000

    result = await _void(order, wiring.proofs.rows[0].id, proofs=wiring.proofs)

    assert result.data.amount_paid_cents == 0
    assert result.data.balance_due_cents == 35000
    assert wiring.proofs.rows[0].status is PaymentProofStatus.REJECTED


@pytest.mark.asyncio
async def test_the_same_receipt_can_be_re_recorded_after_a_void(wiring, paid_calls):
    """Correcting a typo must not need a second photograph of the receipt.

    There is one receipt per transfer. Void-then-re-record with the same
    image was the documented correction path, and the blanket image-hash
    uniqueness made it a dead end.
    """
    order = _order(total=35000)
    receipt = _png((21, 21, 21))

    await _record(order, amount=10000, image=receipt)
    await _void(order, wiring.proofs.rows[0].id, proofs=wiring.proofs)

    corrected = await _record(order, amount=12000, image=receipt)

    assert corrected.data.amount_paid_cents == 12000
    assert corrected.data.balance_due_cents == 23000
    assert len(wiring.proofs.rows) == 2


@pytest.mark.asyncio
async def test_a_rejected_customer_proof_still_blocks_its_own_bytes(wiring, paid_calls):
    """The exemption is for merchant rows only.

    Resubmitting the identical screenshot after a rejection is the replay
    the uniqueness exists to stop, so a customer proof gets no pass.
    """
    import hashlib

    from src.core.entities.instapay import PaymentProof
    from src.infrastructure.external_services.image.proof_sanitizer import (
        sanitize_proof_image,
    )

    order = _order()
    receipt = _png((22, 22, 22))
    sanitized = sanitize_proof_image(receipt, content_type="image/png")
    rejected = PaymentProof.new(
        tenant_id=TENANT_ID,
        store_id=STORE_ID,
        order_id=order.id,
        proof_image_key="k",
        proof_image_hash=hashlib.sha256(sanitized.bytes).digest(),
        transaction_ref="CUST-REJECTED",
        declared_amount_cents=5000,
    )
    rejected.mark_rejected(uuid4(), "not a real receipt")
    wiring.proofs.rows.append(rejected)

    with pytest.raises(HTTPException) as exc:
        await _record(order, amount=5000, image=receipt)

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_the_same_reference_can_be_reused_after_a_void(wiring, paid_calls):
    """Void-then-re-record keeps the rail's own transaction number.

    The reference on the voided row is the only one the merchant has — the
    blanket uniqueness made correcting a typo impossible without inventing
    a fake reference.
    """
    order = _order(total=35000)

    await _record(order, amount=10000, image=_png((23, 23, 23)), reference="VF-7788")
    await _void(order, wiring.proofs.rows[0].id, proofs=wiring.proofs)

    corrected = await _record(
        order, amount=12000, image=_png((24, 24, 24)), reference="VF-7788"
    )

    assert corrected.data.payment.transaction_ref == "VF-7788"
    assert corrected.data.amount_paid_cents == 12000
    assert len(wiring.proofs.rows) == 2


@pytest.mark.asyncio
async def test_a_rejected_customer_proof_still_blocks_its_reference(wiring, paid_calls):
    """The reference exemption is for merchant rows only."""
    from src.core.entities.instapay import PaymentProof

    order = _order()
    rejected = PaymentProof.new(
        tenant_id=TENANT_ID,
        store_id=STORE_ID,
        order_id=order.id,
        proof_image_key="k",
        proof_image_hash=b"other-hash",
        transaction_ref="CUST-REF-1",
        declared_amount_cents=5000,
    )
    rejected.mark_rejected(uuid4(), "not a real receipt")
    wiring.proofs.rows.append(rejected)

    with pytest.raises(HTTPException) as exc:
        await _record(
            order, amount=5000, image=_png((25, 25, 25)), reference="CUST-REF-1"
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_void_response_reflects_an_order_settled_concurrently(wiring):
    """The void answers with a re-read order, not the row from the top.

    A concurrent request may mark the order PAID after this request loaded
    it but before the void lands; the money totals are recomputed post-void
    either way, but the status field must not lag them.
    """

    class _RaceOrderRepo:
        """First read PENDING so the guard passes, second read PAID."""

        def __init__(self, before, after) -> None:
            self.reads = 0
            self.before = before
            self.after = after

        async def get_by_id(self, order_id):
            self.reads += 1
            return self.after if self.reads > 1 else self.before

        async def update(self, order):
            return order

    order = _order(total=35000)
    await _record(order, amount=10000)
    proof_id = wiring.proofs.rows[0].id

    settled = _order(total=35000, id=order.id, payment_status=PaymentStatus.PAID)
    result = await _void(
        order,
        proof_id,
        proofs=wiring.proofs,
        order_repo=_RaceOrderRepo(order, settled),
    )

    assert result.data.order_payment_status == PaymentStatus.PAID.value
    assert result.data.amount_paid_cents == 0
    assert result.data.balance_due_cents == 35000


@pytest.mark.asyncio
async def test_void_is_refused_on_a_paid_order(wiring, paid_calls):
    order = _order(total=10000)
    await _record(order, amount=10000)
    assert order.payment_status is PaymentStatus.PAID

    with pytest.raises(HTTPException) as exc:
        await _void(order, wiring.proofs.rows[0].id, proofs=wiring.proofs)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_void_is_refused_twice(wiring, paid_calls):
    order = _order(total=35000)
    await _record(order, amount=10000)
    proof_id = wiring.proofs.rows[0].id

    await _void(order, proof_id, proofs=wiring.proofs)
    with pytest.raises(HTTPException) as exc:
        await _void(order, proof_id, proofs=wiring.proofs)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_void_refuses_a_customer_submitted_proof(wiring, paid_calls):
    """Those belong to the review panel's reject flow, not to this route."""
    from src.core.entities.instapay import PaymentProof

    order = _order()
    customer_proof = PaymentProof.new(
        tenant_id=TENANT_ID,
        store_id=STORE_ID,
        order_id=order.id,
        proof_image_key="k",
        proof_image_hash=b"h",
        transaction_ref="CUST-1",
        declared_amount_cents=5000,
    )
    customer_proof.mark_approved(uuid4())
    wiring.proofs.rows.append(customer_proof)

    with pytest.raises(HTTPException) as exc:
        await _void(order, customer_proof.id, proofs=wiring.proofs)
    assert exc.value.status_code == 409


# ── Purged receipt image (retention sweeper) ─────────────────────────


@pytest.mark.asyncio
async def test_purged_image_returns_no_url_instead_of_a_broken_one():
    """After 90 days on a terminal order the R2 object is deleted and the
    key nulled. The row — and the amount — stay. Composing a URL for it
    sent the hub after bytes that no longer exist, which surfaced as
    "the signed URL may have expired" plus a Refresh button that could
    never succeed."""
    from src.core.entities.instapay import PaymentProof

    proof = PaymentProof.new(
        tenant_id=TENANT_ID,
        store_id=STORE_ID,
        order_id=uuid4(),
        proof_image_key="",
        proof_image_hash=b"h",
        transaction_ref="OLD-1",
        declared_amount_cents=5000,
    )

    hydrated = await module._hydrate_proof(proof, _FakeStorage())

    assert hydrated.signed_image_url is None
    assert hydrated.declared_amount_cents == 5000


# ── The two SQL queries fakes cannot vouch for ───────────────────────


@pytest.mark.asyncio
async def test_repository_queries_against_a_real_session(test_session):
    """``amount_paid_cents`` sums only settled rows; ``has_recorded_payments``
    is the sweeper's guard against cancelling an order that has money on it."""
    from src.core.entities.instapay import PaymentProof
    from src.infrastructure.repositories.payment_proof_repository import (
        PaymentProofRepository,
    )

    repo = PaymentProofRepository(test_session)
    order_id = uuid4()

    assert await repo.amount_paid_cents(order_id) == 0
    assert await repo.has_recorded_payments(order_id) is False

    def _proof(ref, amount, method):
        return PaymentProof.new(
            tenant_id=TENANT_ID,
            store_id=STORE_ID,
            order_id=order_id,
            proof_image_key=f"k-{ref}",
            proof_image_hash=ref.encode(),
            transaction_ref=ref,
            declared_amount_cents=amount,
            recorded_method=method,
        )

    settled = _proof("A", 10000, "vodafone_cash")
    settled.mark_approved(USER_ID)
    await repo.create(settled)

    voided = _proof("B", 9999, "instapay")
    voided.mark_rejected(USER_ID, "typo")
    await repo.create(voided)

    # A customer upload still awaiting review is not money in hand.
    pending = _proof("C", 7777, None)
    await repo.create(pending)

    assert await repo.amount_paid_cents(order_id) == 10000
    assert await repo.has_recorded_payments(order_id) is True

    settled.mark_rejected(USER_ID, "voided after all")
    await repo.update(settled)

    assert await repo.amount_paid_cents(order_id) == 0
    assert await repo.has_recorded_payments(order_id) is False


@pytest.mark.asyncio
async def test_partial_unique_index_lets_a_voided_receipt_be_reused(test_session):
    """The pre-check and the index must agree.

    If only ``image_hash_exists`` were relaxed, the request would sail past
    the 409 and then die on the unique constraint as a 500. This asserts
    against the real schema-built index, not the in-memory fake.
    """
    from sqlalchemy.exc import IntegrityError

    from src.core.entities.instapay import PaymentProof
    from src.infrastructure.repositories.payment_proof_repository import (
        PaymentProofRepository,
    )

    repo = PaymentProofRepository(test_session)
    order_id = uuid4()
    shared_hash = b"identical-receipt-bytes"

    def _proof(ref):
        return PaymentProof.new(
            tenant_id=TENANT_ID,
            store_id=STORE_ID,
            order_id=order_id,
            proof_image_key=f"k-{ref}",
            proof_image_hash=shared_hash,
            transaction_ref=ref,
            declared_amount_cents=10000,
            recorded_method="vodafone_cash",
        )

    first = _proof("VOIDED")
    first.mark_approved(USER_ID)
    await repo.create(first)

    # While it stands, the hash is taken.
    assert await repo.image_hash_exists(STORE_ID, shared_hash) is True

    first.mark_rejected(USER_ID, "typo")
    await repo.update(first)

    # Voided: the merchant may re-record from the same receipt.
    assert await repo.image_hash_exists(STORE_ID, shared_hash) is False
    second = _proof("CORRECTED")
    second.mark_approved(USER_ID)
    await repo.create(second)

    # ...but only once. The live row holds the hash again.
    assert await repo.image_hash_exists(STORE_ID, shared_hash) is True
    third = _proof("REPLAY")
    third.mark_approved(USER_ID)
    with pytest.raises(IntegrityError):
        await repo.create(third)


@pytest.mark.asyncio
async def test_partial_unique_index_lets_a_voided_reference_be_reused(
    test_session,
):
    """Same agreement as the image hash, for the bank reference.

    Void-then-re-record reuses the rail's own number; the pre-check and the
    index must both allow it, while a live row still blocks a replay.
    """
    from sqlalchemy.exc import IntegrityError

    from src.core.entities.instapay import PaymentProof
    from src.infrastructure.repositories.payment_proof_repository import (
        PaymentProofRepository,
    )

    repo = PaymentProofRepository(test_session)
    order_id = uuid4()
    shared_ref = "VF-7788"

    def _proof(image_tag):
        return PaymentProof.new(
            tenant_id=TENANT_ID,
            store_id=STORE_ID,
            order_id=order_id,
            proof_image_key=f"k-{image_tag}",
            proof_image_hash=image_tag.encode(),
            transaction_ref=shared_ref,
            declared_amount_cents=10000,
            recorded_method="vodafone_cash",
        )

    first = _proof("receipt")
    first.mark_approved(USER_ID)
    await repo.create(first)

    # While it stands, the reference is taken.
    assert await repo.transaction_ref_exists(STORE_ID, shared_ref) is True

    first.mark_rejected(USER_ID, "typo")
    await repo.update(first)

    # Voided: the merchant may re-record with the same reference.
    assert await repo.transaction_ref_exists(STORE_ID, shared_ref) is False
    second = _proof("receipt-again")
    second.mark_approved(USER_ID)
    await repo.create(second)

    # ...but only once. The live row holds the reference again.
    assert await repo.transaction_ref_exists(STORE_ID, shared_ref) is True
    third = _proof("replay")
    third.mark_approved(USER_ID)
    with pytest.raises(IntegrityError):
        await repo.create(third)
