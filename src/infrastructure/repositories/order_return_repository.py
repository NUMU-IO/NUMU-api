"""Repository for OrderReturn rows (Phase 3.1).

Storefront and hub both write here:
  - storefront: create + cancel + read (customer-scoped)
  - hub: read + transition (store-scoped)

We keep the row-level dict <-> entity translation here so the entity
stays free of SQLAlchemy specifics; the line_items field round-trips
through JSONB transparently.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.order_return import (
    OrderReturn,
    ReturnLineItem,
    ReturnReason,
    ReturnStatus,
)
from src.infrastructure.database.models.tenant.order_return import OrderReturnModel


def _line_items_to_dicts(items: list[ReturnLineItem]) -> list[dict]:
    return [
        {
            "order_line_index": li.order_line_index,
            "product_id": str(li.product_id),
            "variant_id": str(li.variant_id) if li.variant_id else None,
            "product_name": li.product_name,
            "quantity": li.quantity,
            "unit_price": li.unit_price,
            "reason": li.reason.value if li.reason else None,
            "customer_note": li.customer_note,
        }
        for li in items
    ]


def _line_items_from_dicts(raw: list[dict] | None) -> list[ReturnLineItem]:
    out: list[ReturnLineItem] = []
    for d in raw or []:
        out.append(
            ReturnLineItem(
                order_line_index=int(d["order_line_index"]),
                product_id=UUID(d["product_id"]),
                variant_id=UUID(d["variant_id"]) if d.get("variant_id") else None,
                product_name=d["product_name"],
                quantity=int(d["quantity"]),
                unit_price=int(d["unit_price"]),
                reason=ReturnReason(d["reason"]) if d.get("reason") else None,
                customer_note=d.get("customer_note"),
            )
        )
    return out


def _to_entity(row: OrderReturnModel) -> OrderReturn:
    return OrderReturn(
        id=row.id,
        tenant_id=row.tenant_id,
        store_id=row.store_id,
        order_id=row.order_id,
        customer_id=row.customer_id,
        return_number=row.return_number,
        status=row.status,
        reason=row.reason,
        customer_note=row.customer_note,
        merchant_note=row.merchant_note,
        line_items=_line_items_from_dicts(row.line_items),
        refund_id=row.refund_id,
        requested_at=row.requested_at,
        approved_at=row.approved_at,
        rejected_at=row.rejected_at,
        received_at=row.received_at,
        completed_at=row.completed_at,
        canceled_at=row.canceled_at,
        approved_by=row.approved_by,
        rejected_by=row.rejected_by,
        received_by=row.received_by,
        requested_amount=row.requested_amount,
        currency=row.currency,
        metadata=row.extra_metadata or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_to_row(row: OrderReturnModel, entity: OrderReturn) -> None:
    row.status = entity.status
    row.reason = entity.reason
    row.customer_note = entity.customer_note
    row.merchant_note = entity.merchant_note
    row.line_items = _line_items_to_dicts(entity.line_items)
    row.refund_id = entity.refund_id
    row.approved_at = entity.approved_at
    row.rejected_at = entity.rejected_at
    row.received_at = entity.received_at
    row.completed_at = entity.completed_at
    row.canceled_at = entity.canceled_at
    row.approved_by = entity.approved_by
    row.rejected_by = entity.rejected_by
    row.received_by = entity.received_by
    row.requested_amount = entity.requested_amount
    row.currency = entity.currency
    row.extra_metadata = entity.metadata or {}
    row.merchant_note = entity.merchant_note


class OrderReturnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, entity: OrderReturn) -> OrderReturn:
        row = OrderReturnModel(
            id=entity.id,
            tenant_id=entity.tenant_id,
            order_id=entity.order_id,
            store_id=entity.store_id,
            customer_id=entity.customer_id,
            return_number=entity.return_number,
            status=entity.status,
            reason=entity.reason,
            customer_note=entity.customer_note,
            merchant_note=entity.merchant_note,
            line_items=_line_items_to_dicts(entity.line_items),
            refund_id=entity.refund_id,
            requested_at=entity.requested_at,
            requested_amount=entity.requested_amount,
            currency=entity.currency,
            extra_metadata=entity.metadata or {},
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return _to_entity(row)

    async def get_by_id(self, return_id: UUID) -> OrderReturn | None:
        row = await self._session.get(OrderReturnModel, return_id)
        return _to_entity(row) if row else None

    async def list_for_order(self, order_id: UUID) -> list[OrderReturn]:
        result = await self._session.execute(
            select(OrderReturnModel)
            .where(OrderReturnModel.order_id == order_id)
            .order_by(desc(OrderReturnModel.requested_at))
        )
        return [_to_entity(r) for r in result.scalars().all()]

    async def list_for_customer(
        self,
        customer_id: UUID,
        limit: int = 50,
    ) -> list[OrderReturn]:
        result = await self._session.execute(
            select(OrderReturnModel)
            .where(OrderReturnModel.customer_id == customer_id)
            .order_by(desc(OrderReturnModel.requested_at))
            .limit(limit)
        )
        return [_to_entity(r) for r in result.scalars().all()]

    async def list_for_store(
        self,
        store_id: UUID,
        status: ReturnStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OrderReturn]:
        query = (
            select(OrderReturnModel)
            .where(OrderReturnModel.store_id == store_id)
            .order_by(desc(OrderReturnModel.requested_at))
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            query = query.where(OrderReturnModel.status == status)
        result = await self._session.execute(query)
        return [_to_entity(r) for r in result.scalars().all()]

    async def update(self, entity: OrderReturn) -> OrderReturn:
        row = await self._session.get(OrderReturnModel, entity.id)
        if row is None:
            raise ValueError(f"OrderReturn {entity.id} not found")
        _apply_to_row(row, entity)
        await self._session.commit()
        await self._session.refresh(row)
        return _to_entity(row)
