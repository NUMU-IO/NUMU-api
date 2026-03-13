"""Payment reconciliation service.

Compares PAID orders against payment_transactions for a given day,
flags mismatches, and persists a reconciliation run with details.

Supported mismatch types:
- paid_order_no_transaction  — order.payment_status=PAID but no matching transaction
- transaction_no_order       — transaction exists with no linked order
- amount_mismatch            — order total != transaction amount

Run daily via Celery beat at 02:00 UTC (after end-of-day settlement).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.core.entities.reconciliation import (
    MismatchType,
    PaymentReconciliationRun,
    ReconciliationMismatch,
    ReconciliationStatus,
)
from src.infrastructure.database.models.public.reconciliation import (
    PaymentReconciliationRunModel,
    ReconciliationMismatchModel,
)
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
)

logger = get_logger(__name__)

# Status values used by the payment gateway webhook handlers
_SUCCESSFUL_TX_STATUSES = {"success", "paid", "completed", "captured"}


class ReconciliationService:
    """Reconcile paid orders against payment transaction records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_for_date(self, target_date: date) -> PaymentReconciliationRun:
        """Run full reconciliation for a calendar day (UTC).

        Creates a run record, compares orders vs transactions, persists all
        mismatches, then marks the run completed (or failed on error).
        """
        period_start = datetime(
            target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=UTC
        )
        period_end = period_start + timedelta(days=1)

        run = await self._create_run("all", period_start, period_end)
        log = logger.bind(run_id=str(run.id), date=str(target_date))
        log.info("reconciliation_started")

        try:
            run = await self._execute_run(run, period_start, period_end)
        except Exception as exc:
            log.error("reconciliation_failed", error=str(exc))
            run = await self._mark_failed(run, str(exc))

        return run

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create_run(
        self, gateway: str, period_start: datetime, period_end: datetime
    ) -> PaymentReconciliationRun:
        model = PaymentReconciliationRunModel(
            gateway=gateway,
            period_start=period_start,
            period_end=period_end,
            status=ReconciliationStatus.RUNNING.value,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._run_to_entity(model)

    async def _execute_run(
        self,
        run: PaymentReconciliationRun,
        period_start: datetime,
        period_end: datetime,
    ) -> PaymentReconciliationRun:
        # --- 1. Fetch PAID orders for the period ---
        # Import here to avoid circular dependency at module load time
        from src.infrastructure.database.models.tenant.order import OrderModel

        paid_orders_result = await self.session.execute(
            select(
                OrderModel.id,
                OrderModel.order_number,
                OrderModel.total,
                OrderModel.payment_method,
                OrderModel.payment_id,
                OrderModel.paid_at,
            ).where(
                and_(
                    OrderModel.payment_status == "PAID",
                    OrderModel.paid_at >= period_start,
                    OrderModel.paid_at < period_end,
                )
            )
        )
        paid_orders = paid_orders_result.all()

        # --- 2. Fetch successful payment_transactions for the period ---
        txns_result = await self.session.execute(
            select(
                PaymentTransactionModel.id,
                PaymentTransactionModel.order_id,
                PaymentTransactionModel.amount_cents,
                PaymentTransactionModel.gateway,
                PaymentTransactionModel.gateway_transaction_id,
                PaymentTransactionModel.status,
                PaymentTransactionModel.processing_completed_at,
            ).where(
                and_(
                    PaymentTransactionModel.status.in_(_SUCCESSFUL_TX_STATUSES),
                    PaymentTransactionModel.processing_completed_at >= period_start,
                    PaymentTransactionModel.processing_completed_at < period_end,
                )
            )
        )
        txns = txns_result.all()

        # --- 3. Build lookup maps ---
        order_id_set: set[UUID] = {row.id for row in paid_orders}
        txn_by_order: dict[UUID, list] = {}
        for txn in txns:
            if txn.order_id:
                txn_by_order.setdefault(txn.order_id, []).append(txn)

        mismatches: list[ReconciliationMismatch] = []
        expected_cents = 0
        actual_cents = 0

        # --- 4. Orders with no matching transaction ---
        for order in paid_orders:
            # Convert order.total (Decimal) to cents
            order_cents = int(float(order.total) * 100) if order.total else 0
            expected_cents += order_cents

            linked_txns = txn_by_order.get(order.id, [])
            if not linked_txns:
                mismatches.append(
                    ReconciliationMismatch(
                        run_id=run.id,
                        mismatch_type=MismatchType.PAID_ORDER_NO_TRANSACTION,
                        order_id=order.id,
                        order_number=order.order_number,
                        expected_amount_cents=order_cents,
                        notes=(
                            f"Order {order.order_number} is PAID but has no "
                            f"matching payment transaction record"
                        ),
                    )
                )
            else:
                # Check amount matches the most recent successful transaction
                latest_txn = linked_txns[-1]
                actual_cents += latest_txn.amount_cents
                if (
                    abs(latest_txn.amount_cents - order_cents) > 1
                ):  # 1-piastre tolerance
                    mismatches.append(
                        ReconciliationMismatch(
                            run_id=run.id,
                            mismatch_type=MismatchType.AMOUNT_MISMATCH,
                            order_id=order.id,
                            order_number=order.order_number,
                            transaction_id=latest_txn.id,
                            gateway_transaction_id=latest_txn.gateway_transaction_id,
                            expected_amount_cents=order_cents,
                            actual_amount_cents=latest_txn.amount_cents,
                            gateway=latest_txn.gateway,
                            notes=(
                                f"Order total {order_cents} piastres vs "
                                f"transaction {latest_txn.amount_cents} piastres"
                            ),
                        )
                    )

        # --- 5. Transactions with no linked order ---
        for txn in txns:
            if txn.order_id and txn.order_id not in order_id_set:
                mismatches.append(
                    ReconciliationMismatch(
                        run_id=run.id,
                        mismatch_type=MismatchType.TRANSACTION_NO_ORDER,
                        transaction_id=txn.id,
                        gateway_transaction_id=txn.gateway_transaction_id,
                        actual_amount_cents=txn.amount_cents,
                        gateway=txn.gateway,
                        notes=(
                            f"Transaction {txn.gateway_transaction_id} references "
                            f"order {txn.order_id} which is not PAID for this period"
                        ),
                    )
                )

        # --- 6. Persist mismatches ---
        for mm in mismatches:
            self.session.add(
                ReconciliationMismatchModel(
                    run_id=mm.run_id,
                    mismatch_type=mm.mismatch_type.value,
                    order_id=mm.order_id,
                    order_number=mm.order_number,
                    transaction_id=mm.transaction_id,
                    gateway_transaction_id=mm.gateway_transaction_id,
                    expected_amount_cents=mm.expected_amount_cents,
                    actual_amount_cents=mm.actual_amount_cents,
                    gateway=mm.gateway,
                    notes=mm.notes,
                )
            )

        # --- 7. Update run record ---
        result = await self.session.execute(
            select(PaymentReconciliationRunModel).where(
                PaymentReconciliationRunModel.id == run.id
            )
        )
        run_model = result.scalar_one()
        run_model.status = ReconciliationStatus.COMPLETED.value
        run_model.total_orders_checked = len(paid_orders)
        run_model.total_transactions_checked = len(txns)
        run_model.mismatches_found = len(mismatches)
        run_model.expected_amount_cents = expected_cents
        run_model.actual_amount_cents = actual_cents
        run_model.completed_at = datetime.now(UTC)

        await self.session.flush()
        await self.session.refresh(run_model)

        logger.info(
            "reconciliation_completed",
            run_id=str(run.id),
            orders=len(paid_orders),
            transactions=len(txns),
            mismatches=len(mismatches),
        )

        return self._run_to_entity(run_model)

    async def _mark_failed(
        self, run: PaymentReconciliationRun, error: str
    ) -> PaymentReconciliationRun:
        result = await self.session.execute(
            select(PaymentReconciliationRunModel).where(
                PaymentReconciliationRunModel.id == run.id
            )
        )
        run_model = result.scalar_one()
        run_model.status = ReconciliationStatus.FAILED.value
        run_model.error_message = error[:500]
        run_model.completed_at = datetime.now(UTC)
        await self.session.flush()
        await self.session.refresh(run_model)
        return self._run_to_entity(run_model)

    @staticmethod
    def _run_to_entity(m: PaymentReconciliationRunModel) -> PaymentReconciliationRun:
        return PaymentReconciliationRun(
            id=m.id,
            gateway=m.gateway,
            period_start=m.period_start,
            period_end=m.period_end,
            status=ReconciliationStatus(m.status),
            total_orders_checked=m.total_orders_checked,
            total_transactions_checked=m.total_transactions_checked,
            mismatches_found=m.mismatches_found,
            expected_amount_cents=m.expected_amount_cents,
            actual_amount_cents=m.actual_amount_cents,
            error_message=m.error_message,
            completed_at=m.completed_at,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
