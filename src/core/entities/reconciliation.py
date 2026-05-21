"""Payment reconciliation domain entities."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.core.entities.base import BaseEntity


class ReconciliationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MismatchType(StrEnum):
    PAID_ORDER_NO_TRANSACTION = "paid_order_no_transaction"
    TRANSACTION_NO_ORDER = "transaction_no_order"
    AMOUNT_MISMATCH = "amount_mismatch"
    DUPLICATE_TRANSACTION = "duplicate_transaction"


class PaymentReconciliationRun(BaseEntity):
    """A single reconciliation run covering a time window, scoped to a store."""

    store_id: UUID | None = None
    gateway: str  # 'paymob', 'fawry', 'all'
    period_start: datetime
    period_end: datetime
    status: ReconciliationStatus = ReconciliationStatus.PENDING
    total_orders_checked: int = 0
    total_transactions_checked: int = 0
    mismatches_found: int = 0
    # Amounts in smallest currency unit (cents/piastres)
    expected_amount_cents: int = 0
    actual_amount_cents: int = 0
    error_message: str | None = None
    completed_at: datetime | None = None


class ReconciliationMismatch(BaseEntity):
    """A single discrepancy found during a reconciliation run."""

    run_id: UUID
    mismatch_type: MismatchType
    order_id: UUID | None = None
    order_number: str | None = None
    transaction_id: UUID | None = None
    gateway_transaction_id: str | None = None
    expected_amount_cents: int | None = None
    actual_amount_cents: int | None = None
    gateway: str | None = None
    notes: str | None = None
    resolved: bool = False
    resolved_at: datetime | None = None
    resolved_by: str | None = None
