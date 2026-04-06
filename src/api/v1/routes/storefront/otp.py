"""Storefront COD OTP verification routes.

URL: /storefront/store/{store_id}/checkout/otp

Generates and verifies OTP codes for COD orders to reduce fake orders.
OTP is sent via WhatsApp (primary) or email (fallback).
"""

import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from src.api.dependencies.auth import get_current_customer
from src.api.responses import SuccessResponse
from src.config import settings
from src.core.entities.customer import Customer
from src.infrastructure.cache.redis_cache import RedisCacheService

logger = logging.getLogger(__name__)

router = APIRouter()

_cache_service: RedisCacheService | None = (
    RedisCacheService() if settings.redis_host else None
)

OTP_TTL_SECONDS = 300  # 5 minutes
OTP_COOLDOWN_SECONDS = 60  # 1 minute between resends
OTP_MAX_ATTEMPTS = 5  # Max verification attempts before lockout


def _otp_key(store_id: UUID, customer_id: UUID) -> str:
    return f"cod_otp:{store_id}:{customer_id}"


def _otp_attempts_key(store_id: UUID, customer_id: UUID) -> str:
    return f"cod_otp_attempts:{store_id}:{customer_id}"


def _otp_cooldown_key(store_id: UUID, customer_id: UUID) -> str:
    return f"cod_otp_cooldown:{store_id}:{customer_id}"


def _otp_verified_key(store_id: UUID, customer_id: UUID) -> str:
    return f"cod_otp_verified:{store_id}:{customer_id}"


class SendOtpRequest(BaseModel):
    phone: str = Field(..., description="Customer phone number for OTP delivery")


class SendOtpResponse(BaseModel):
    sent: bool
    channel: str = Field(description="Delivery channel: whatsapp or email")
    message: str


class VerifyOtpRequest(BaseModel):
    otp_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class VerifyOtpResponse(BaseModel):
    verified: bool


@router.post(
    "/otp/send",
    response_model=SuccessResponse[SendOtpResponse],
    summary="Send COD verification OTP",
    operation_id="send_cod_otp",
)
async def send_cod_otp(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: SendOtpRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
):
    """Send a 6-digit OTP to the customer's phone via WhatsApp.

    Used to verify COD orders and reduce fake/fraudulent orders.
    OTP expires after 5 minutes. Cooldown of 60 seconds between sends.
    """
    if not _cache_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OTP service is not available",
        )

    # Verify customer belongs to this store
    if current_customer.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer does not belong to this store",
        )

    # Cooldown check — prevent spamming
    cooldown_key = _otp_cooldown_key(store_id, current_customer.id)
    if await _cache_service.exists(cooldown_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="يرجى الانتظار قبل طلب كود جديد",  # Please wait before requesting a new code
        )

    # Generate 6-digit OTP
    otp_code = f"{secrets.randbelow(1000000):06d}"

    # Store in Redis
    otp_key = _otp_key(store_id, current_customer.id)
    await _cache_service.set(otp_key, otp_code, expire=OTP_TTL_SECONDS)

    # Reset attempts counter
    attempts_key = _otp_attempts_key(store_id, current_customer.id)
    await _cache_service.delete(attempts_key)

    # Clear any previous verified state
    verified_key = _otp_verified_key(store_id, current_customer.id)
    await _cache_service.delete(verified_key)

    # Set cooldown
    await _cache_service.set(cooldown_key, "1", expire=OTP_COOLDOWN_SECONDS)

    # Send OTP via WhatsApp (primary) or email (fallback)
    channel = "whatsapp"
    sent = False

    if settings.whatsapp_enabled:
        try:
            from src.infrastructure.external_services.whatsapp.messaging_service import (
                WhatsAppMessagingService,
            )

            wa_service = WhatsAppMessagingService()
            phone = wa_service._format_phone_number(request.phone)

            # Use WhatsApp authentication message template
            # Meta's authentication templates use the OTP button format
            import httpx

            url = f"https://graph.facebook.com/v18.0/{wa_service.phone_number_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": "cod_otp_verification",
                    "language": {"code": "ar"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": otp_code},
                            ],
                        },
                        {
                            "type": "button",
                            "sub_type": "url",
                            "index": "0",
                            "parameters": [
                                {"type": "text", "text": otp_code},
                            ],
                        },
                    ],
                },
            }

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers=wa_service._get_headers(),
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    sent = True
                    logger.info(
                        f"COD OTP sent via WhatsApp to {phone} for customer {current_customer.id}"
                    )
                else:
                    logger.warning(
                        f"WhatsApp OTP send failed: {resp.status_code} {resp.text}"
                    )
        except Exception as e:
            logger.warning(f"WhatsApp OTP send failed: {e}")

    # Fallback to email if WhatsApp failed or not configured
    if not sent:
        channel = "email"
        customer_email = str(current_customer.email) if current_customer.email else None
        if customer_email:
            try:
                from src.core.interfaces.services.email_service import EmailMessage
                from src.infrastructure.external_services.resend.email_service import (
                    ResendEmailService,
                )
                from src.infrastructure.external_services.resend.email_templates.transactional import (
                    otp_code_email,
                )

                email_service = ResendEmailService()
                tpl = otp_code_email(code=otp_code, purpose="order", expires_minutes=5)
                await email_service.send_email(
                    EmailMessage(
                        to=customer_email,
                        subject=tpl["subject"],
                        html_content=tpl["html"],
                    )
                )
                sent = True
                logger.info(
                    f"COD OTP sent via email to {customer_email} for customer {current_customer.id}"
                )
            except Exception as e:
                logger.warning(f"Email OTP send failed: {e}")

    if not sent:
        # Clean up if we couldn't send
        await _cache_service.delete(otp_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="فشل في إرسال كود التحقق. حاول مرة أخرى.",
        )

    return SuccessResponse(
        data=SendOtpResponse(
            sent=True,
            channel=channel,
            message="تم إرسال كود التحقق"
            if channel == "whatsapp"
            else "تم إرسال كود التحقق على بريدك الإلكتروني",
        ),
        message="OTP sent successfully",
    )


@router.post(
    "/otp/verify",
    response_model=SuccessResponse[VerifyOtpResponse],
    summary="Verify COD OTP",
    operation_id="verify_cod_otp",
)
async def verify_cod_otp(
    store_id: Annotated[UUID, Path(description="Store ID")],
    request: VerifyOtpRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
):
    """Verify the OTP code submitted by the customer.

    After successful verification, the customer can proceed with COD checkout.
    Max 5 attempts before the OTP is invalidated.
    """
    if not _cache_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OTP service is not available",
        )

    if current_customer.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer does not belong to this store",
        )

    # Check attempts
    attempts_key = _otp_attempts_key(store_id, current_customer.id)
    attempts = await _cache_service.get(attempts_key)
    if attempts and int(attempts) >= OTP_MAX_ATTEMPTS:
        # Invalidate the OTP
        otp_key = _otp_key(store_id, current_customer.id)
        await _cache_service.delete(otp_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="تم تجاوز عدد المحاولات. يرجى طلب كود جديد.",
        )

    # Get stored OTP
    otp_key = _otp_key(store_id, current_customer.id)
    stored_otp = await _cache_service.get(otp_key)

    if not stored_otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="كود التحقق منتهي الصلاحية. يرجى طلب كود جديد.",
        )

    # Increment attempts
    current_attempts = int(attempts) if attempts else 0
    await _cache_service.set(
        attempts_key, str(current_attempts + 1), expire=OTP_TTL_SECONDS
    )

    # Verify
    if request.otp_code != stored_otp:
        remaining = OTP_MAX_ATTEMPTS - (current_attempts + 1)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"كود التحقق غير صحيح. متبقي {remaining} محاولات.",
        )

    # OTP is correct — mark as verified and clean up
    await _cache_service.delete(otp_key)
    await _cache_service.delete(attempts_key)

    # Set verified flag (valid for 15 minutes — enough time to complete checkout)
    verified_key = _otp_verified_key(store_id, current_customer.id)
    await _cache_service.set(verified_key, "1", expire=900)

    logger.info(
        f"COD OTP verified for customer {current_customer.id} in store {store_id}"
    )

    return SuccessResponse(
        data=VerifyOtpResponse(verified=True),
        message="تم التحقق بنجاح",
    )
