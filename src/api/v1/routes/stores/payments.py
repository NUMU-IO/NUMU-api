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


class BalancesResponse(BaseModel):
    wallet_balance_cents: int
    store_balance_cents: int


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
        PaymentTransactionModel.status == "successful",
    )
    result = await db.execute(q)
    store_balance = result.scalar() or 0

    # Also count from paid orders if no payment transactions exist
    if store_balance == 0:
        q_orders = select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            OrderModel.store_id == store_id,
            OrderModel.payment_status == "PAID",
        )
        result_orders = await db.execute(q_orders)
        store_balance = result_orders.scalar() or 0

    return SuccessResponse(
        data=BalancesResponse(
            wallet_balance_cents=0,
            store_balance_cents=store_balance,
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
            OrderModel.payment_status.in_(["PAID", "AUTHORIZED"]),
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
                if str(order.payment_status) == "PAID"
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
