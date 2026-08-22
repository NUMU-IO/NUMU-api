"""Payment routes nested under stores.

URL: /stores/{store_id}/payments
Provides balances, transaction history, and invoice listing for the merchant finance page.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.invoice import InvoiceModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
)

router = APIRouter(prefix="/{store_id}/payments")


# ── Response schemas ────────────────────────────────────────────────


# Every gateway webhook and the InstaPay proof flow write ``status="success"``
# on PaymentTransactionModel. This endpoint used to filter on the literal
# ``"successful"`` — which no writer ever produced — so the transaction sum
# was always 0 and the hero number silently fell back to ``SUM(orders.total)``.
# Accept both spellings so any legacy rows still count.
SUCCESS_STATUSES: tuple[str, ...] = ("success", "successful")
PENDING_STATUSES: tuple[str, ...] = ("pending", "processing", "authorized", "initiated")
COD_IN_TRANSIT_ORDER_STATUSES: tuple[str, ...] = ("confirmed", "processing", "shipped")


class BalancesResponse(BaseModel):
    wallet_balance_cents: int
    store_balance_cents: int
    # Sum of gateway transactions that are still settling. Previously the
    # hub derived this from whichever 20 rows were on the current page.
    pending_clearance_cents: int = 0
    # COD orders that are confirmed/processing/shipped but not yet paid —
    # cash the courier still owes the merchant.
    cod_in_transit_cents: int = 0
    cod_in_transit_count: int = 0
    # Which source produced ``store_balance_cents``: "transactions" when
    # there is at least one successful gateway transaction, else "orders"
    # (sum of paid orders). The hub labels the number accordingly instead
    # of presenting a silent fallback as a ledger balance.
    store_balance_source: str = "transactions"


class TransactionResponse(BaseModel):
    id: str
    order_id: str | None
    amount_cents: int
    currency: str
    status: str
    payment_method: str
    gateway: str
    customer_name: str | None
    customer_email: str | None
    created_at: str
    reference_id: str | None


class InvoiceResponse(BaseModel):
    id: str
    service: str
    amount_cents: int
    currency: str
    payment_status: str
    approved_at: str | None
    created_at: str


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/balances")
async def get_balances(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[BalancesResponse]:
    """Return aggregated wallet and store payment balances."""
    store_id = store.id

    # Store balance = sum of successful payment transactions
    q = select(func.coalesce(func.sum(PaymentTransactionModel.amount_cents), 0)).where(
        PaymentTransactionModel.store_id == store_id,
        PaymentTransactionModel.status.in_(SUCCESS_STATUSES),
    )
    result = await db.execute(q)
    store_balance = int(result.scalar() or 0)
    store_balance_source = "transactions"

    # Stores that only ever took COD / manual payments have no gateway
    # transactions at all; fall back to paid orders but say so.
    if store_balance == 0:
        q_orders = select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            OrderModel.store_id == store_id,
            OrderModel.payment_status == "paid",
        )
        result_orders = await db.execute(q_orders)
        store_balance = int(result_orders.scalar() or 0)
        store_balance_source = "orders"

    pending_clearance = int(
        (
            await db.execute(
                select(
                    func.coalesce(func.sum(PaymentTransactionModel.amount_cents), 0)
                ).where(
                    PaymentTransactionModel.store_id == store_id,
                    PaymentTransactionModel.status.in_(PENDING_STATUSES),
                )
            )
        ).scalar()
        or 0
    )

    cod_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(OrderModel.total), 0),
                func.count(OrderModel.id),
            ).where(
                OrderModel.store_id == store_id,
                OrderModel.payment_method == "cod",
                OrderModel.payment_status != "paid",
                OrderModel.status.in_(COD_IN_TRANSIT_ORDER_STATUSES),
            )
        )
    ).one()
    cod_in_transit_cents = int(cod_row[0] or 0)
    cod_in_transit_count = int(cod_row[1] or 0)

    # Wallet = the tenant's prepaid platform wallet (shared across the
    # tenant's stores). Zero when no wallet row exists yet.
    from src.infrastructure.database.models.public.wallet import (
        MerchantWalletModel,
    )

    wallet_balance = (
        await db.execute(
            select(MerchantWalletModel.balance_cents).where(
                MerchantWalletModel.tenant_id == store.tenant_id
            )
        )
    ).scalar_one_or_none() or 0

    return SuccessResponse(
        data=BalancesResponse(
            wallet_balance_cents=wallet_balance,
            store_balance_cents=store_balance,
            pending_clearance_cents=pending_clearance,
            cod_in_transit_cents=cod_in_transit_cents,
            cod_in_transit_count=cod_in_transit_count,
            store_balance_source=store_balance_source,
        )
    )


@router.get("/transactions")
async def list_transactions(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> SuccessResponse[list[TransactionResponse]]:
    """List payment transactions for the store, newest first."""
    store_id = store.id

    # Try payment_transactions table first
    count_q = select(func.count(PaymentTransactionModel.id)).where(
        PaymentTransactionModel.store_id == store_id,
    )
    count_result = await db.execute(count_q)
    tx_count = count_result.scalar() or 0

    if tx_count > 0:
        # Join with orders → customers for customer info
        order_alias = aliased(OrderModel)
        customer_alias = aliased(CustomerModel)

        q = (
            select(
                PaymentTransactionModel,
                customer_alias.first_name,
                customer_alias.last_name,
                customer_alias.email,
            )
            .outerjoin(
                order_alias,
                PaymentTransactionModel.order_id == order_alias.id,
            )
            .outerjoin(
                customer_alias,
                order_alias.customer_id == customer_alias.id,
            )
            .where(PaymentTransactionModel.store_id == store_id)
            .order_by(PaymentTransactionModel.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(q)
        rows = result.all()

        transactions = []
        for row in rows:
            tx = row[0]
            first_name = row[1]
            last_name = row[2]
            email = row[3]
            customer_name = (
                f"{first_name} {last_name}".strip()
                if first_name or last_name
                else tx.display_name
            )
            transactions.append(
                TransactionResponse(
                    id=str(tx.id),
                    order_id=str(tx.order_id) if tx.order_id else None,
                    amount_cents=tx.amount_cents,
                    currency=tx.currency or "EGP",
                    status=tx.status,
                    payment_method=tx.gateway or "card",
                    gateway=tx.gateway or "",
                    customer_name=customer_name or None,
                    customer_email=email,
                    created_at=tx.created_at.isoformat() if tx.created_at else "",
                    reference_id=tx.gateway_transaction_id,
                )
            )
        return SuccessResponse(data=transactions)

    # Fallback: derive transactions from paid orders
    q = (
        select(
            OrderModel,
            CustomerModel.first_name,
            CustomerModel.last_name,
            CustomerModel.email,
        )
        .outerjoin(CustomerModel, OrderModel.customer_id == CustomerModel.id)
        .where(
            OrderModel.store_id == store_id,
            OrderModel.payment_status.in_(["paid", "authorized"]),
        )
        .order_by(OrderModel.paid_at.desc().nullslast(), OrderModel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(q)
    rows = result.all()

    transactions = []
    for row in rows:
        order = row[0]
        first_name = row[1]
        last_name = row[2]
        email = row[3]
        customer_name = (
            f"{first_name} {last_name}".strip() if first_name or last_name else None
        )
        transactions.append(
            TransactionResponse(
                id=str(order.id),
                order_id=str(order.id),
                amount_cents=order.total,
                currency=order.currency or "EGP",
                status="successful"
                if str(order.payment_status).lower() in ("paid", "authorized")
                else "pending",
                payment_method=order.payment_method or "card",
                gateway=order.payment_method or "",
                customer_name=customer_name,
                customer_email=email,
                created_at=(order.paid_at or order.created_at).isoformat()
                if (order.paid_at or order.created_at)
                else "",
                reference_id=order.payment_id,
            )
        )

    return SuccessResponse(data=transactions)


@router.get("/invoices")
async def list_invoices(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[list[InvoiceResponse]]:
    """List invoices for the store, newest first."""
    store_id = store.id

    q = (
        select(InvoiceModel)
        .where(InvoiceModel.store_id == store_id)
        .order_by(InvoiceModel.created_at.desc())
        .limit(50)
    )
    result = await db.execute(q)
    models = result.scalars().all()

    invoice_type_labels = {
        "I": "Invoice",
        "C": "Credit Note",
        "D": "Debit Note",
    }

    invoices = []
    for m in models:
        inv_type = str(m.invoice_type.value) if m.invoice_type else "I"
        status = str(m.status.value) if m.status else "draft"
        invoices.append(
            InvoiceResponse(
                id=m.invoice_number or str(m.id),
                service=invoice_type_labels.get(inv_type, "Invoice"),
                amount_cents=m.total or 0,
                currency=m.currency or "EGP",
                payment_status=status,
                approved_at=(
                    m.updated_at.isoformat()
                    if status in ("accepted", "submitted") and m.updated_at
                    else None
                ),
                created_at=m.created_at.isoformat() if m.created_at else "",
            )
        )

    return SuccessResponse(data=invoices)
