"""Paymob webhook handler.

Receives payment notifications from Paymob for:
- Card payments (processed/declined)
- Wallet payments (processed/declined)
- Refunds
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.config import settings
from src.infrastructure.external_services.paymob import PaymobPaymentService

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize service
paymob_service = PaymobPaymentService()


@router.post("/callback")
async def paymob_callback(
    request: Request,
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

    # Process based on event type
    if is_refunded:
        # Handle refund notification
        logger.info(f"Paymob refund processed for order {order_id}")
        # TODO: Update order status in database
        # await order_service.mark_refunded(order_id, amount_cents)

    elif is_voided:
        # Handle void notification
        logger.info(f"Paymob payment voided for order {order_id}")
        # TODO: Update order status in database
        # await order_service.mark_cancelled(order_id)

    elif success:
        # Handle successful payment
        logger.info(f"Paymob payment successful for order {order_id}: {amount_cents} {currency}")
        # TODO: Update order status in database
        # await order_service.mark_paid(order_id, transaction_id, amount_cents)

    else:
        # Handle failed payment
        error_msg = obj.get("data", {}).get("message", "Payment failed")
        logger.warning(f"Paymob payment failed for order {order_id}: {error_msg}")
        # TODO: Update order status in database
        # await order_service.mark_payment_failed(order_id, error_msg)

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
