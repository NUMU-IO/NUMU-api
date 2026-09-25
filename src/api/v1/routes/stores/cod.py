"""COD protection for apps: one place to read and change a store's COD rules.

URL: /stores/{store_id}/cod/settings (scope ``cod:read`` / ``cod:write``)

The COD rules live in four settings blocks with four merchant routes (Trust
Network, the deposit policy under payment, WhatsApp tap-to-confirm, the
checkout phone OTP). An app like COD Shield manages them together, and
apps never hold ``settings:*``, so this route reads them as one bundle and
writes each section through the merchant route that owns it: the same
validation, the same side effects, one source of truth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_onboarding_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.routes.stores import settings as settings_routes
from src.api.v1.routes.stores import whatsapp as whatsapp_routes
from src.api.v1.schemas.tenant.settings import (
    CodDepositPolicy,
    UpdateCodTrustRequest,
    UpdatePaymentSettingsRequest,
)
from src.core.checkout_fields import CheckoutFieldsConfig
from src.core.entities.order import OrderStatus
from src.core.entities.store import Store
from src.core.events.order_events import OrderStatusChangedEvent
from src.core.logging import get_logger
from src.infrastructure.repositories import OnboardingRepository, StoreRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/{store_id}/cod")


class Confirmation(BaseModel):
    """WhatsApp tap-to-confirm before a COD order ships."""

    require_order_confirmation: bool = False
    delay_minutes: int = 0


class Otp(BaseModel):
    """The phone OTP at checkout. ``available`` is whether this store can
    send one at all (platform switch, WhatsApp access, a GOWA number)."""

    require_verification: bool = True
    available: bool = False


class CodSettings(BaseModel):
    trust: dict
    deposit: CodDepositPolicy
    confirmation: Confirmation
    otp: Otp


class ConfirmationUpdate(BaseModel):
    require_order_confirmation: bool | None = None
    delay_minutes: int | None = Field(default=None, ge=0, le=1440)


class OtpUpdate(BaseModel):
    require_verification: bool


class CodSettingsUpdate(BaseModel):
    """Any subset of the sections; each is validated by its own route."""

    trust: UpdateCodTrustRequest | None = None
    deposit: CodDepositPolicy | None = None
    confirmation: ConfirmationUpdate | None = None
    otp: OtpUpdate | None = None


async def _read(store: Store, db: AsyncSession) -> CodSettings:
    from src.application.services.checkout_identity import otp_available
    from src.core.checkout_fields import resolve_config

    settings = store.settings or {}
    payment = settings_routes._build_payment_response(settings.get("payment", {}))
    notifications = settings.get("whatsapp_notifications") or {}
    identity = resolve_config(settings)["identity"]
    return CodSettings(
        trust=settings_routes._get_cod_trust_settings(settings),
        deposit=payment.cod_deposit_policy,
        confirmation=Confirmation(
            require_order_confirmation=bool(
                notifications.get("require_order_confirmation", False)
            ),
            delay_minutes=int(
                (settings.get("whatsapp") or {}).get("confirm_order_delay_minutes") or 0
            ),
        ),
        otp=Otp(
            require_verification=bool(identity.get("require_verification", True)),
            available=await otp_available(store.id, settings, db),
        ),
    )


@router.get(
    "/settings",
    response_model=SuccessResponse[CodSettings],
    summary="Read the store's COD protection rules",
    operation_id="get_cod_settings",
)
async def get_cod_settings(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return SuccessResponse(data=await _read(store, db))


@router.patch(
    "/settings",
    response_model=SuccessResponse[CodSettings],
    summary="Change the store's COD protection rules",
    operation_id="update_cod_settings",
)
async def update_cod_settings(
    body: CodSettingsUpdate,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Each section goes through the merchant route that owns it, so a
    deposit gateway that isn't configured, or confirmation without
    WhatsApp access, is refused here exactly as it is in the hub."""
    if body.trust is not None:
        await settings_routes.update_cod_trust_settings_endpoint(
            request=body.trust, store=store, store_repo=store_repo
        )
    if body.deposit is not None:
        await settings_routes.update_payment_settings(
            request=UpdatePaymentSettingsRequest(cod_deposit_policy=body.deposit),
            store=store,
            store_repo=store_repo,
            onboarding_repo=onboarding_repo,
        )
    if body.confirmation is not None:
        c = body.confirmation
        if c.require_order_confirmation is not None:
            await whatsapp_routes.byo_update_notifications(
                body={"require_order_confirmation": c.require_order_confirmation},
                store=store,
                db=db,
            )
        if c.delay_minutes is not None:
            await whatsapp_routes.update_whatsapp_settings(
                body=whatsapp_routes.WhatsAppSettingsUpdate(
                    confirm_order_delay_minutes=c.delay_minutes
                ),
                store=store,
                db=db,
            )
    if body.otp is not None:
        from src.core.checkout_fields import resolve_config

        cfg = resolve_config(store.settings or {})
        cfg["identity"] = {
            **cfg["identity"],
            "require_verification": body.otp.require_verification,
        }
        await settings_routes.update_checkout_fields(
            payload=CheckoutFieldsConfig.model_validate(cfg),
            store=store,
            store_repo=store_repo,
        )
    return SuccessResponse(data=await _read(store, db), message="COD settings saved")


# ─── Review hold ─────────────────────────────────────────────────


class HeldOrder(BaseModel):
    order_id: str
    order_number: str
    total: int
    currency: str
    created_at: str
    customer_name: str | None
    phone_last4: str | None
    risk_score: int | None
    risk_level: str | None
    factors: list


class ReviewQueue(BaseModel):
    items: list[HeldOrder]
    total: int


class RejectRequest(BaseModel):
    reason: str = Field(default="cod_review_rejected", max_length=200)


@router.get(
    "/review",
    response_model=SuccessResponse[ReviewQueue],
    summary="COD orders held for review",
    operation_id="list_cod_review",
)
async def list_held_orders(
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Pending COD orders the Trust Network held (action "hold"), newest
    first, each with its latest risk assessment."""
    from sqlalchemy import func, select

    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.risk_assessment import (
        RiskAssessmentModel,
    )

    held = (
        OrderModel.store_id == store.id,
        OrderModel.cod_review_status == "held",
        OrderModel.status == OrderStatus.PENDING,
    )
    total = await db.scalar(select(func.count()).select_from(OrderModel).where(*held))
    orders = (
        (
            await db.execute(
                select(OrderModel)
                .where(*held)
                .order_by(OrderModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    risks: dict = {}
    if orders:
        rows = (
            (
                await db.execute(
                    select(RiskAssessmentModel)
                    .where(RiskAssessmentModel.order_id.in_([o.id for o in orders]))
                    .order_by(RiskAssessmentModel.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            risks[r.order_id] = r  # the newest wins
    items = []
    for o in orders:
        address = o.shipping_address or {}
        phone = str(address.get("phone") or "")
        name = " ".join(
            p for p in (address.get("first_name"), address.get("last_name")) if p
        )
        risk = risks.get(o.id)
        items.append(
            HeldOrder(
                order_id=str(o.id),
                order_number=o.order_number,
                total=o.total,
                currency=o.currency,
                created_at=o.created_at.isoformat(),
                customer_name=name or None,
                phone_last4=phone[-4:] or None,
                risk_score=risk.risk_score if risk else None,
                risk_level=risk.risk_level if risk else None,
                factors=(risk.factors or []) if risk else [],
            )
        )
    return SuccessResponse(data=ReviewQueue(items=items, total=int(total or 0)))


async def _held_order(db: AsyncSession, store: Store, order_id: UUID):
    from src.infrastructure.repositories.order_repository import OrderRepository

    repo = OrderRepository(db)
    order = await repo.get_by_id(order_id)
    if order is None or order.store_id != store.id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.cod_review_status != "held":
        raise HTTPException(
            status_code=409, detail="This order is not held for review."
        )
    return repo, order


async def _publish_status(
    db: AsyncSession, store: Store, order, previous: str, new: str, reason: str
) -> None:
    """The same event a status change in the hub sends, so courier booking,
    notifications, the activity log and webhooks all follow."""
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.customer import CustomerModel
    from src.infrastructure.events.setup import get_event_bus

    try:
        cust = (
            await db.execute(
                select(CustomerModel).where(CustomerModel.id == order.customer_id)
            )
        ).scalar_one_or_none()
        get_event_bus().publish(
            OrderStatusChangedEvent(
                order_id=order.id,
                order_number=order.order_number,
                store_id=order.store_id,
                store_name=store.name or "",
                customer_id=order.customer_id,
                customer_email=str(cust.email) if cust and cust.email else None,
                customer_phone=str(cust.phone) if cust and cust.phone else None,
                customer_name=(
                    f"{cust.first_name} {cust.last_name}".strip() if cust else None
                ),
                previous_status=previous,
                new_status=new,
                reason=reason,
                language=store.default_language or "ar",
            )
        )
    except Exception:
        logger.exception("cod_review_event_failed", order_id=str(order.id))


@router.post(
    "/orders/{order_id}/approve",
    response_model=SuccessResponse[dict],
    summary="Release a held COD order",
    operation_id="approve_cod_review",
)
async def approve_held_order(
    order_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Confirm the order (PENDING to CONFIRMED), which books the courier."""
    repo, order = await _held_order(db, store, order_id)
    previous = getattr(order.status, "value", order.status)
    confirmed = order.status == OrderStatus.PENDING
    if confirmed:
        order.confirm()
    order.cod_review_status = "approved"
    order.cod_reviewed_at = datetime.now(UTC)
    updated = await repo.update(order)
    await db.commit()
    if confirmed:
        await _publish_status(
            db,
            store,
            updated,
            previous,
            OrderStatus.CONFIRMED.value,
            "cod_review_approved",
        )
    return SuccessResponse(
        data={
            "order_id": str(updated.id),
            "status": getattr(updated.status, "value", updated.status),
        },
        message="Order approved",
    )


@router.post(
    "/orders/{order_id}/reject",
    response_model=SuccessResponse[dict],
    summary="Cancel a held COD order",
    operation_id="reject_cod_review",
)
async def reject_held_order(
    order_id: UUID,
    body: RejectRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Cancel the order and put its stock back."""
    from src.application.services.stock_service import try_restock_order

    repo, order = await _held_order(db, store, order_id)
    previous = getattr(order.status, "value", order.status)
    try:
        order.cancel(body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    order.cod_review_status = "rejected"
    order.cod_reviewed_at = datetime.now(UTC)
    await try_restock_order(db, order, reason=body.reason)
    updated = await repo.update(order)
    await db.commit()
    await _publish_status(
        db, store, updated, previous, OrderStatus.CANCELLED.value, body.reason
    )
    return SuccessResponse(
        data={"order_id": str(updated.id), "status": OrderStatus.CANCELLED.value},
        message="Order cancelled",
    )


# ─── Deposit request on an existing order ─────────────────────────


class DepositRequest(BaseModel):
    """Exactly one of ``amount_cents`` or ``percent``."""

    amount_cents: int | None = Field(default=None, ge=100)
    percent: int | None = Field(default=None, ge=1, le=90)
    ttl_minutes: int = Field(default=1440, ge=5, le=10080)


class DepositLink(BaseModel):
    order_id: str
    deposit_cents: int
    balance_due_cents: int
    expires_at: str
    pay_url: str


@router.post(
    "/orders/{order_id}/deposit",
    response_model=SuccessResponse[DepositLink],
    summary="Ask for a confirmation deposit on a COD order",
    operation_id="request_cod_deposit",
)
async def request_deposit(
    order_id: UUID,
    body: DepositRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Turn a pending COD order into one waiting for a deposit.

    The order moves to ``pending_deposit`` exactly as a checkout deposit
    order does, so the rest is the existing flow: the customer pays the
    deposit on the store's /pay page, the payment webhook confirms the
    order (which books the courier), and an unpaid deposit expires and
    cancels it. The balance stays cash on delivery. How the link reaches
    the customer (WhatsApp, SMS) is the caller's choice.
    """
    from src.infrastructure.repositories.order_repository import OrderRepository

    if (body.amount_cents is None) == (body.percent is None):
        raise HTTPException(
            status_code=422, detail="Send exactly one of amount_cents or percent."
        )
    repo = OrderRepository(db)
    order = await repo.get_by_id(order_id)
    if order is None or order.store_id != store.id:
        raise HTTPException(status_code=404, detail="Order not found")
    if (order.payment_method or "").lower() != "cod":
        raise HTTPException(status_code=409, detail="Only a COD order takes a deposit.")
    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail="Only a pending order can be switched to a deposit.",
        )
    deposit = (
        body.amount_cents
        if body.amount_cents is not None
        else order.total * body.percent // 100
    )
    if not 100 <= deposit < order.total:
        raise HTTPException(
            status_code=422,
            detail="The deposit must be at least 1.00 and less than the order total.",
        )
    expires = datetime.now(UTC) + timedelta(minutes=body.ttl_minutes)
    # Checkout assigns PENDING_DEPOSIT the same way: it is not a transition
    # a pending order makes on its own.
    order.status = OrderStatus.PENDING_DEPOSIT
    order.deposit_required_cents = deposit
    order.deposit_amount_cents = deposit
    order.deposit_expires_at = expires
    if order.cod_review_status == "held":
        order.cod_review_status = "deposit"
        order.cod_reviewed_at = datetime.now(UTC)
    updated = await repo.update(order)
    await db.commit()
    return SuccessResponse(
        data=DepositLink(
            order_id=str(updated.id),
            deposit_cents=deposit,
            balance_due_cents=updated.total - deposit,
            expires_at=expires.isoformat(),
            pay_url=f"{store.store_url}/pay/{updated.id}",
        ),
        message="Deposit requested",
    )
