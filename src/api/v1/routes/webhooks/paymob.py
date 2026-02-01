"""Paymob webhook handler.

Receives payment notifications from Paymob for:
- Card payments (processed/declined)
- Wallet payments (processed/declined)
- Refunds
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.config import settings
from src.infrastructure.external_services.paymob import PaymobPaymentService
from src.infrastructure.repositories.order_repository import OrderRepository

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize service
paymob_service = PaymobPaymentService()


@router.post("/callback")
async def paymob_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    hmac: str = Header(None, alias="hmac"),
):
    """Handle Paymob payment callback.

    Paymob sends a POST request with payment transaction details.
    The HMAC header contains the signature for verification.

    Events handled:
    - Transaction approved (success=true)
    - Transaction declined (success=false)
    - Refund processed (is_refunded=true)
    """
    payload = await request.body()

    # Verify signature
    if settings.paymob_hmac_secret:
        verified_data = paymob_service.verify_webhook_signature(payload, hmac or "")
        if not verified_data:
            logger.warning("Paymob webhook signature verification failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )
        data = verified_data
    else:
        # In development, accept without verification
        import json
        data = json.loads(payload)
        logger.warning("Paymob webhook received without signature verification (dev mode)")

    # Extract transaction details
    obj = data.get("obj", {})
    transaction_id = obj.get("id")
    order_id = obj.get("order", {}).get("id")
    merchant_order_id = obj.get("order", {}).get("merchant_order_id")
    success = obj.get("success", False)
    is_refunded = obj.get("is_refunded", False)
    is_voided = obj.get("is_voided", False)
    amount_cents = obj.get("amount_cents", 0)
    currency = obj.get("currency", "EGP")

    # Log the event
    logger.info(
        f"Paymob webhook: transaction={transaction_id}, order={order_id}, "
        f"success={success}, refunded={is_refunded}, voided={is_voided}"
    )

    order_repo = OrderRepository(db)

    # Resolve the internal order via merchant_order_id (our payment_id)
    # or fall back to the Paymob order_id.
    order = None
    lookup_id = merchant_order_id or str(order_id) if order_id else None
    if lookup_id:
        order = await order_repo.get_by_payment_id(lookup_id)

    if not order:
        logger.warning(f"Paymob webhook: could not find order for id={lookup_id}")
        return {"status": "received", "transaction_id": transaction_id}

    # Process based on event type
    if is_refunded:
        logger.info(f"Paymob refund processed for order {order.order_number}")
        order.refund(reason=f"Paymob refund - transaction {transaction_id}")
        await order_repo.update(order)

    elif is_voided:
        logger.info(f"Paymob payment voided for order {order.order_number}")
        if order.can_be_cancelled:
            order.cancel(reason=f"Paymob void - transaction {transaction_id}")
            await order_repo.update(order)
        else:
            logger.warning(
                f"Cannot cancel order {order.order_number} in status {order.status}"
            )

    elif success:
        logger.info(
            f"Paymob payment successful for order {order.order_number}: "
            f"{amount_cents} {currency}"
        )
        order.mark_as_paid(
            payment_id=str(transaction_id),
            payment_method="paymob",
        )
        await order_repo.update(order)

    else:
        error_msg = obj.get("data", {}).get("message", "Payment failed")
        logger.warning(
            f"Paymob payment failed for order {order.order_number}: {error_msg}"
        )
        order.mark_payment_failed(reason=error_msg)
        await order_repo.update(order)

    # Always return 200 to acknowledge receipt
    return {"status": "received", "transaction_id": transaction_id}


@router.get("/callback")
async def paymob_callback_redirect(
    success: bool = False,
    txn_response_code: str | None = None,
    order: str | None = None,
    merchant_order_id: str | None = None,
):
    """Handle Paymob redirect after payment.

    After customer completes payment in iframe, they are redirected here.
    This is for redirect handling, not webhook processing.

    Query params from Paymob:
    - success: Payment success status
    - txn_response_code: Transaction response code
    - order: Paymob order ID
    - merchant_order_id: Your order ID
    """
    logger.info(
        f"Paymob redirect: success={success}, order={order}, "
        f"merchant_order={merchant_order_id}, response_code={txn_response_code}"
    )

    # In production, redirect to frontend with status
    # For now, return status info
    return {
        "success": success,
        "order_id": merchant_order_id or order,
        "message": "Payment completed" if success else "Payment failed",
    }
