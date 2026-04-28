"""Repository for PaymentProof rows."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.instapay import PaymentProof, PaymentProofStatus
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.database.models.tenant.payment_proof import (
    PaymentProofModel,
)

_UINT64_MOD = 1 << 64
_INT64_MAX = (1 << 63) - 1


def _phash_to_db(value: int | None) -> int | None:
    # imagehash returns an unsigned 64-bit int; Postgres BIGINT is signed.
    # Map values above 2^63-1 into the negative half so asyncpg accepts them.
    if value is None:
        return None
    return value - _UINT64_MOD if value > _INT64_MAX else value


def _phash_from_db(value: int | None) -> int | None:
    if value is None:
        return None
    return value + _UINT64_MOD if value < 0 else value


class PaymentProofRepository:
    """Persist and query customer-submitted payment proofs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _tenant_filter(self, query):
        tid = get_tenant_id()
        if tid:
            return query.where(PaymentProofModel.tenant_id == tid)
        return query

    def _to_entity(self, model: PaymentProofModel) -> PaymentProof:
        return PaymentProof(
            id=model.id,
            tenant_id=model.tenant_id,
            store_id=model.store_id,
            order_id=model.order_id,
            proof_image_key=model.proof_image_key,
            proof_image_hash=model.proof_image_hash,
            transaction_ref=model.transaction_ref,
            declared_amount_cents=model.declared_amount_cents,
            status=model.status,
            review_decision_by=model.review_decision_by,
            review_decision_at=model.review_decision_at,
            rejection_reason=model.rejection_reason,
            idempotency_key=model.idempotency_key,
            perceptual_hash=_phash_from_db(model.perceptual_hash),
            ocr_status=model.ocr_status,
            ocr_extracted_amount_cents=model.ocr_extracted_amount_cents,
            ocr_extracted_ipa=model.ocr_extracted_ipa,
            ocr_raw_text=model.ocr_raw_text,
            ocr_provider=model.ocr_provider,
            ocr_processed_at=model.ocr_processed_at,
            ocr_extracted_note=model.ocr_extracted_note,
            ocr_extracted_transaction_ref=model.ocr_extracted_transaction_ref,
            ocr_extracted_recipient_name=model.ocr_extracted_recipient_name,
            auto_approval_block_reasons=model.auto_approval_block_reasons,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def create(self, proof: PaymentProof) -> PaymentProof:
        model = PaymentProofModel(
            id=proof.id,
            tenant_id=proof.tenant_id,
            store_id=proof.store_id,
            order_id=proof.order_id,
            proof_image_key=proof.proof_image_key,
            proof_image_hash=proof.proof_image_hash,
            transaction_ref=proof.transaction_ref,
            declared_amount_cents=proof.declared_amount_cents,
            status=proof.status,
            review_decision_by=proof.review_decision_by,
            review_decision_at=proof.review_decision_at,
            rejection_reason=proof.rejection_reason,
            idempotency_key=proof.idempotency_key,
            perceptual_hash=_phash_to_db(proof.perceptual_hash),
            ocr_status=proof.ocr_status,
            ocr_extracted_amount_cents=proof.ocr_extracted_amount_cents,
            ocr_extracted_ipa=proof.ocr_extracted_ipa,
            ocr_raw_text=proof.ocr_raw_text,
            ocr_provider=proof.ocr_provider,
            ocr_processed_at=proof.ocr_processed_at,
            ocr_extracted_note=proof.ocr_extracted_note,
            ocr_extracted_transaction_ref=proof.ocr_extracted_transaction_ref,
            ocr_extracted_recipient_name=proof.ocr_extracted_recipient_name,
            auto_approval_block_reasons=proof.auto_approval_block_reasons,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def get_by_id(self, proof_id: UUID) -> PaymentProof | None:
        query = select(PaymentProofModel).where(PaymentProofModel.id == proof_id)
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_idempotency_key(
        self, store_id: UUID, idempotency_key: str
    ) -> PaymentProof | None:
        """Scoped to (store, key) to match the uniqueness constraint.

        The sweeper clears keys older than 30 days, so a replay of an
        ancient key will simply miss this lookup and create a fresh
        row — matching the security / storage trade-off for TTL'd
        idempotency keys.
        """
        query = select(PaymentProofModel).where(
            PaymentProofModel.store_id == store_id,
            PaymentProofModel.idempotency_key == idempotency_key,
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def clear_old_idempotency_keys(
        self,
        *,
        older_than: datetime,
        limit: int = 1000,
    ) -> int:
        """Null-out idempotency_key on proofs older than the cutoff.

        Used by the Celery sweeper — keeps the uniqueness constraint
        from accumulating forever while preserving the proof row
        itself for audit. Batched via LIMIT so one sweep can't lock
        the table under heavy load.
        """
        from sqlalchemy import update

        stmt = (
            update(PaymentProofModel)
            .where(
                PaymentProofModel.idempotency_key.is_not(None),
                PaymentProofModel.created_at < older_than,
                PaymentProofModel.id.in_(
                    select(PaymentProofModel.id)
                    .where(
                        PaymentProofModel.idempotency_key.is_not(None),
                        PaymentProofModel.created_at < older_than,
                    )
                    .limit(limit)
                ),
            )
            .values(idempotency_key=None)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def get_latest_for_order(self, order_id: UUID) -> PaymentProof | None:
        query = (
            select(PaymentProofModel)
            .where(PaymentProofModel.order_id == order_id)
            .order_by(PaymentProofModel.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(self._tenant_filter(query))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_for_order(self, order_id: UUID) -> list[PaymentProof]:
        query = (
            select(PaymentProofModel)
            .where(PaymentProofModel.order_id == order_id)
            .order_by(PaymentProofModel.created_at.asc())
        )
        result = await self.session.execute(self._tenant_filter(query))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def update(self, proof: PaymentProof) -> PaymentProof:
        query = select(PaymentProofModel).where(PaymentProofModel.id == proof.id)
        result = await self.session.execute(query)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"PaymentProof {proof.id} not found")
        model.status = proof.status
        model.review_decision_by = proof.review_decision_by
        model.review_decision_at = proof.review_decision_at
        model.rejection_reason = proof.rejection_reason
        # Phase C OCR fields are written after the initial ``create`` —
        # the use case calls vision after persistence and then folds
        # the result back via ``update``. Mirroring the existing
        # status/reason pattern keeps the use-case logic uniform.
        model.ocr_status = proof.ocr_status
        model.ocr_extracted_amount_cents = proof.ocr_extracted_amount_cents
        model.ocr_extracted_ipa = proof.ocr_extracted_ipa
        model.ocr_raw_text = proof.ocr_raw_text
        model.ocr_provider = proof.ocr_provider
        model.ocr_processed_at = proof.ocr_processed_at
        model.updated_at = datetime.now(UTC)
        await self.session.flush()
        return self._to_entity(model)

    async def image_hash_exists(self, store_id: UUID, image_hash: bytes) -> bool:
        """Returns True if the same screenshot was already uploaded in this store."""
        query = select(PaymentProofModel.id).where(
            and_(
                PaymentProofModel.store_id == store_id,
                PaymentProofModel.proof_image_hash == image_hash,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    async def transaction_ref_exists(
        self, store_id: UUID, transaction_ref: str
    ) -> bool:
        """Returns True if the same bank ref was already used in this store."""
        query = select(PaymentProofModel.id).where(
            and_(
                PaymentProofModel.store_id == store_id,
                PaymentProofModel.transaction_ref == transaction_ref,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    async def list_purgeable_images(
        self,
        *,
        older_than: datetime,
        limit: int = 500,
    ) -> list[PaymentProof]:
        """Proofs whose R2 image can be deleted to reclaim storage.

        Selects rows where:
          * the proof was created before ``older_than``
          * ``proof_image_key`` is still non-empty (not yet purged)
          * the parent order is in a terminal state that means no
            reviewer will need to look at the image again:
              - CANCELLED / REFUNDED  → dispute window closed
              - DELIVERED             → fulfilment confirmed

        We keep the DB row (audit trail) and only null out the key.
        """
        from src.core.entities.order import OrderStatus
        from src.infrastructure.database.models.tenant.order import OrderModel

        terminal = {
            OrderStatus.CANCELLED.value,
            OrderStatus.REFUNDED.value,
            OrderStatus.DELIVERED.value,
        }
        query = (
            select(PaymentProofModel)
            .join(OrderModel, OrderModel.id == PaymentProofModel.order_id)
            .where(
                PaymentProofModel.proof_image_key.is_not(None),
                PaymentProofModel.proof_image_key != "",
                PaymentProofModel.created_at < older_than,
                OrderModel.status.in_(terminal),
            )
            .limit(limit)
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def clear_image_key(self, proof_id: UUID) -> None:
        """Set ``proof_image_key`` to empty once the R2 object is gone."""
        from sqlalchemy import update

        await self.session.execute(
            update(PaymentProofModel)
            .where(PaymentProofModel.id == proof_id)
            .values(proof_image_key="")
        )

    async def find_perceptual_neighbours(
        self,
        store_id: UUID,
        phash: int,
        *,
        max_distance: int,
        since: datetime,
        limit: int = 500,
    ) -> list[tuple[PaymentProof, int]]:
        """Return prior proofs whose pHash is within ``max_distance`` Hamming bits.

        Used by the Phase A dedup layer (close match → 409) and the
        Phase B merchant-review hint (loose match → "possibly related"
        panel). Postgres has no native Hamming-distance index without
        an extension, so we narrow with the ``(store_id,
        perceptual_hash)`` btree index and the ``since`` window, then
        compute exact distances in Python on the returned rows.

        Bounded by ``limit`` so a long-tenured store can't blow the
        per-call work — at 500 the Python pass is ~1 ms.

        Returns each candidate with its computed distance so the caller
        can either take the min (dedup) or sort/filter (review hint).
        """
        query = (
            select(PaymentProofModel)
            .where(
                PaymentProofModel.store_id == store_id,
                PaymentProofModel.perceptual_hash.is_not(None),
                PaymentProofModel.created_at >= since,
            )
            .order_by(PaymentProofModel.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(self._tenant_filter(query))
        models = result.scalars().all()

        out: list[tuple[PaymentProof, int]] = []
        for m in models:
            # bit_count() on Python int — O(1) on CPython 3.10+, no
            # numpy / imagehash import needed for the hot path.
            stored = _phash_from_db(m.perceptual_hash)
            if stored is None:
                continue
            distance = (stored ^ phash).bit_count()
            if distance <= max_distance:
                out.append((self._to_entity(m), distance))
        return out

    async def daily_auto_approve_stats(
        self,
        store_id: UUID,
        *,
        since: datetime,
    ) -> tuple[int, int]:
        """Return (count, total_cents) of auto-approvals since the cutoff.

        Powers the per-store daily cap. Joins against orders indirectly
        via declared_amount_cents (the caller falls back to order.total
        when declared is None).
        """
        query = select(
            func.count(PaymentProofModel.id),
            func.coalesce(func.sum(PaymentProofModel.declared_amount_cents), 0),
        ).where(
            and_(
                PaymentProofModel.store_id == store_id,
                PaymentProofModel.status == PaymentProofStatus.AUTO_APPROVED,
                PaymentProofModel.created_at >= since,
            )
        )
        result = await self.session.execute(query)
        row = result.one()
        return int(row[0]), int(row[1])
