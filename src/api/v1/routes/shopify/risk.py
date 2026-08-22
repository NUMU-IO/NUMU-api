"""Risk scoring endpoints — list risk orders, take actions, resend nudges."""

from __future__ import annotations

import logging
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.shopify import (
    get_risk_assessment_repo,
    get_shopify_installation_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import (
    ResendVerificationRequest,
    ResendVerificationResponse,
    RiskActionRequest,
    RiskOrderResponse,
)
from src.infrastructure.repositories.shopify_repository import (
    RiskAssessmentRepository,
    ShopifyInstallationRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


def _to_response(model) -> RiskOrderResponse:
    return RiskOrderResponse(
        id=str(model.id),
        order_number=model.order_number,
        customer_name=model.customer_name,
        customer_email=model.customer_email,
        total_cents=model.total_cents,
        currency=model.currency,
        payment_method=model.payment_method,
        risk_score=model.risk_score,
        risk_level=model.risk_level,
        score_type=model.score_type,
        suggested_action=model.suggested_action,
        action_taken=model.action_taken,
        factors=model.factors or [],
        scored_at=model.scored_at,
        created_at=model.created_at,
    )


@router.get(
    "/{store_id}/risk/orders",
    response_model=SuccessResponse[list[RiskOrderResponse]],
    summary="List risk-scored orders",
    operation_id="shopify_list_risk_orders",
)
async def list_risk_orders(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[RiskAssessmentRepository, Depends(get_risk_assessment_repo)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    shopify_order_id: str | None = Query(
        None,
        max_length=255,
        description=(
            "Filter to a single Shopify order. Used by the order-risk-card "
            "admin block extension to fetch one record per order page render."
        ),
    ),
):
    models = await repo.list_by_store(
        store_id,
        limit=limit,
        offset=offset,
        shopify_order_id=shopify_order_id,
    )
    return SuccessResponse(data=[_to_response(m) for m in models])


@router.post(
    "/{store_id}/risk/orders/{order_id}/action",
    response_model=SuccessResponse[RiskOrderResponse],
    summary="Take action on a risky order",
    operation_id="shopify_risk_order_action",
)
async def take_risk_action(
    store_id: Annotated[UUID, Path()],
    order_id: Annotated[UUID, Path()],
    request: RiskActionRequest,
    repo: Annotated[RiskAssessmentRepository, Depends(get_risk_assessment_repo)],
    install_repo: Annotated[
        ShopifyInstallationRepository, Depends(get_shopify_installation_repo)
    ],
):
    model = await repo.update_action(order_id, request.action, store_id=store_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Risk assessment not found",
        )

    message = f"Action '{request.action}' recorded"

    # The manual "WhatsApp confirm" button must actually message the buyer,
    # not just annotate the row — same send path as the automation action.
    if request.action == "whatsapp_confirm":
        nudge_note = await _enqueue_manual_nudge(model, install_repo)
        message = f"{message} — {nudge_note}"

    return SuccessResponse(data=_to_response(model), message=message)


async def _enqueue_manual_nudge(model, install_repo) -> str:
    """Best-effort nudge dispatch for a manually confirmed risk order.

    The assessment row only keeps a phone *hash*, so the raw phone is
    resolved from Shopify at dispatch time. Failures never fail the
    action — the caller surfaces the note in the response message.
    """
    if not model.shopify_order_id:
        return "nudge skipped: no shopify_order_id on the assessment"

    installation = await install_repo.get_by_store_id(UUID(str(model.store_id)))
    if not installation:
        return "nudge skipped: no Shopify installation for the store"

    from src.infrastructure.external_services.shopify.admin_client import (
        get_order_contact,
    )

    contact = await get_order_contact(
        installation.shopify_domain,
        installation.access_token_encrypted,
        model.shopify_order_id,
    )
    if not contact or not contact.get("phone"):
        return "nudge skipped: no customer phone on the Shopify order"

    try:
        from src.infrastructure.messaging.tasks.whatsapp_nudge_task import (
            send_whatsapp_nudge,
        )

        send_whatsapp_nudge.delay(
            store_id=str(model.store_id),
            shopify_order_id=model.shopify_order_id,
            amount_cents=model.total_cents,
            currency=model.currency,
            customer_phone=contact["phone"],
            customer_name=model.customer_name or contact.get("customer_name") or "",
            order_number=model.order_number or contact.get("order_number") or "",
            shop_domain=installation.shopify_domain,
            store_name=contact.get("shop_name") or "",
        )
        return "WhatsApp nudge queued"
    except Exception as exc:
        logger.error("manual nudge enqueue failed for assessment %s: %s", model.id, exc)
        return "nudge skipped: queue unavailable"


@router.post(
    # ``:path`` so the gid form (gid://shopify/Order/N — decoded slashes)
    # still matches; the static /resend-verification suffix anchors the end.
    "/{store_id}/risk/orders/{shopify_order_id:path}/resend-verification",
    response_model=SuccessResponse[ResendVerificationResponse],
    summary="Re-send the WhatsApp pay-online verification for an order",
    operation_id="shopify_resend_verification",
)
async def resend_verification(
    store_id: Annotated[UUID, Path()],
    shopify_order_id: Annotated[str, Path(max_length=255)],
    repo: Annotated[RiskAssessmentRepository, Depends(get_risk_assessment_repo)],
    install_repo: Annotated[
        ShopifyInstallationRepository, Depends(get_shopify_installation_repo)
    ],
    session: Annotated[AsyncSession, Depends(get_db)],
    body: ResendVerificationRequest | None = None,
):
    """Synchronous send used by the Shopify Flow "Send WhatsApp verification"
    action. Returns ``sent`` + Meta ``message_id`` so Flow workflows can
    branch on the outcome.

    ``shopify_order_id`` accepts the numeric id (what the webhook processor
    persists) or the full ``gid://shopify/Order/…`` form.
    """
    # Clients URL-encode the gid form; %2F survives path matching, so
    # decode before normalizing.
    shopify_order_id = unquote(shopify_order_id)

    # Normalize both id forms — webhook rows store the numeric id.
    candidates = [shopify_order_id]
    if shopify_order_id.startswith("gid://"):
        candidates.append(shopify_order_id.rsplit("/", 1)[-1])
    else:
        candidates.append(f"gid://shopify/Order/{shopify_order_id}")

    model = None
    for candidate in candidates:
        rows = await repo.list_by_store(store_id, limit=1, shopify_order_id=candidate)
        if rows:
            model = rows[0]
            break
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No risk assessment found for this order",
        )

    installation = await install_repo.get_by_store_id(store_id)
    if not installation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Shopify installation for this store",
        )

    from src.infrastructure.external_services.shopify.admin_client import (
        get_order_contact,
    )

    contact = await get_order_contact(
        installation.shopify_domain,
        installation.access_token_encrypted,
        model.shopify_order_id or shopify_order_id,
    )
    if not contact or not contact.get("phone"):
        return SuccessResponse(
            data=ResendVerificationResponse(sent=False, reason="no_phone"),
            message="Customer phone not available on the Shopify order",
        )

    from src.application.services.shopify_nudge_service import (
        create_payment_link_session,
        send_conversion_nudge,
        store_display_name,
    )

    pls = await create_payment_link_session(
        session,
        store_id=store_id,
        shopify_order_id=model.shopify_order_id or shopify_order_id,
        amount_cents=model.total_cents,
        currency=model.currency,
    )
    # Commit before the network send so the buyer's payment URL can never
    # reference a row a later rollback would erase.
    await session.commit()

    result = await send_conversion_nudge(
        phone=contact["phone"],
        customer_name=model.customer_name or contact.get("customer_name") or "",
        order_number=model.order_number or contact.get("order_number") or "",
        store_name=store_display_name(
            installation.shopify_domain, contact.get("shop_name")
        ),
        amount_cents=model.total_cents,
        currency=model.currency,
        payment_session_id=str(pls.id),
        language=(body.language if body and body.language else "ar"),
    )

    return SuccessResponse(
        data=ResendVerificationResponse(
            sent=result.sent,
            message_id=result.message_id,
            reason=result.error,
        ),
        message="Verification sent" if result.sent else "Verification not sent",
    )
