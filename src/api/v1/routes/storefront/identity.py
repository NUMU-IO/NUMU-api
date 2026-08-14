"""Phone-first checkout identity — storefront OTP issue / verify / status.

The browser-facing half of the checkout-identity feature. The customer types
their phone, gets a WhatsApp code, types it back; on success this cart
session is marked verified (Redis flag, 24h) and — when the phone matches an
existing customer — the customer is logged in on the spot (auth cookies) so
checkout prefills exactly as it does for a returning account.

Auth model: the ``numu_cart_session`` cookie via ``get_cart_owner`` — the
same anonymous-but-not-nobody gate the cart routes use. Every route also
requires the path ``store_id`` to match the cart owner's resolved store, so
a token from one store can never act on another.

Anti-enumeration: ``issue`` reveals nothing about whether the phone belongs
to a known customer — the same response either way. Existence is only
revealed AFTER the caller has proven ownership of the phone (``verify``,
``customer_known``), at which point it is their own account.

Privacy: ``otp_codes`` stores only HMAC hashes (phone + code). ``verify``
therefore takes the phone again and matches its hash against the row —
binding the attempt to the phone without the cleartext ever being stored.

The sibling Shopify integration route (``routes/shopify/otp.py``) shares the
same ``otp_service`` primitives and table but is internal-key gated and has
no cart/session semantics; it stays untouched.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.utils.cookies import set_customer_auth_cookies
from src.api.v1.routes.storefront._cart_owner import CartOwner, get_cart_owner
from src.application.services.checkout_identity import (
    OTP_RESEND_COOLDOWN_SECONDS,
    otp_available,
    read_identity_flag,
    write_identity_flag,
)
from src.application.services.network_reputation_service import (
    extract_phone_hash_from_string,
)
from src.application.services.otp_service import (
    OTP_MAX_ATTEMPTS,
    OTP_MAX_ISSUES_PER_HOUR,
    OtpVerdict,
    evaluate_verify,
    expires_at_for_now,
    generate_code,
    hash_code,
)
from src.config import get_settings
from src.core.checkout_fields import resolve_config
from src.core.events.otp_events import OtpVerifiedEvent
from src.core.value_objects.phone import InvalidPhoneError, PhoneNumber
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.tenant.customer import CustomerModel
from src.infrastructure.database.models.tenant.otp_code import OtpCodeModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.events.setup import get_event_bus

logger = logging.getLogger(__name__)

router = APIRouter()

# Synthesized guest emails (checkout.py) are placeholders, never contact info
# — must not leak into a "your account" prefill as if the customer typed it.
_GUEST_EMAIL_DOMAIN = "@noemail.numueg.app"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class IdentityOtpIssueRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=25)
    language: Literal["ar", "en"] = "ar"


class IdentityOtpIssueResponse(BaseModel):
    otp_id: str
    expires_at: datetime
    resend_after: int


class IdentityOtpVerifyRequest(BaseModel):
    otp_id: str
    code: str = Field(min_length=4, max_length=10)
    phone: str = Field(min_length=8, max_length=25)


class IdentityProfile(BaseModel):
    first_name: str
    last_name: str
    email: str | None = None
    phone: str


class IdentityOtpVerifyResponse(BaseModel):
    verdict: str
    attempts_left: int
    customer_known: bool = False
    profile: IdentityProfile | None = None


class IdentityStatusResponse(BaseModel):
    required: bool
    otp_available: bool
    verified: bool
    phone_masked: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_store_match(cart_owner: CartOwner, store_id: UUID) -> None:
    """The path store must be the store the cookie/host resolved to."""
    if cart_owner.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "store_mismatch"},
        )


def _canonical_phone(raw: str) -> str:
    try:
        return PhoneNumber.parse(raw.strip(), default_region="EG").e164
    except InvalidPhoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_phone", "message": str(exc)},
        ) from exc


def _cart_key_str(cart_owner: CartOwner) -> str:
    owner_id, _store = cart_owner.cart_key
    return str(owner_id)


def _mask_phone(phone_e164: str) -> str:
    """+201001234567 -> +20••••••4567 — enough to recognise, not to dial."""
    if len(phone_e164) < 7:
        return phone_e164
    return f"{phone_e164[:3]}{'•' * (len(phone_e164) - 7)}{phone_e164[-4:]}"


async def _issue_rate_limits(
    cache: RedisCacheService, store_id: UUID, phone_hash: str
) -> None:
    """Cooldown + hourly ceiling, both per (store, phone), both in Redis.

    Redis being down fails OPEN with a loud log: the ceilings protect cost
    and the merchant's number, but a customer standing at checkout who can't
    receive a code is a lost order — same trade the GOWA guard makes.
    """
    cooldown_key = f"otp:cooldown:{store_id}:{phone_hash}"
    hour_key = (
        f"otp:issue:{store_id}:{phone_hash}:h:{datetime.now(UTC).strftime('%Y%m%d%H')}"
    )
    try:
        acquired = await cache.set_if_absent(
            cooldown_key, "1", expire=OTP_RESEND_COOLDOWN_SECONDS
        )
        if not acquired:
            # set_if_absent returns False BOTH when the key exists and when
            # Redis is down. Only the former is a cooldown hit — a Redis
            # outage must fail open, not freeze every OTP for 45s forever.
            if await cache.exists(cooldown_key):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "otp_cooldown",
                        "retry_after": OTP_RESEND_COOLDOWN_SECONDS,
                    },
                    headers={"Retry-After": str(OTP_RESEND_COOLDOWN_SECONDS)},
                )
            logger.error("otp_issue_cooldown_unavailable_limits_not_enforced")
            return

        # increment returns 0 (not raises) when Redis is down → fail open.
        issued = await cache.increment(hour_key)
        if issued:
            await cache.set(hour_key, issued, expire=7200)
            if int(issued) > OTP_MAX_ISSUES_PER_HOUR:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={"code": "otp_hourly_limit", "retry_after": 3600},
                    headers={"Retry-After": "3600"},
                )
    except HTTPException:
        raise
    except Exception:
        logger.exception("otp_issue_rate_limit_unavailable")


async def _send_otp(
    session: AsyncSession,
    *,
    store_id: UUID,
    tenant_id: UUID,
    store_name: str,
    phone_e164: str,
    code: str,
    language: str,
) -> bool:
    """Deliver the code over the store's WhatsApp transport.

    Deliberately NOT routed through the notification send-guard
    (``whatsapp_send_guard.check``): that guard exists for merchant-initiated
    notifications — its opt-out gate must not block a code the customer just
    requested, and its approval gate is already expressed through
    ``otp_available``. The transport's own protections (GOWA device guard,
    pacing, health) still apply inside the provider.
    """
    from src.core.interfaces.services.messaging_service import (
        MessageContent,
        MessageRecipient,
        MessageType,
    )
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service

    service = await get_whatsapp_service(store_id, session, tenant_id)
    result = await service.send_message(
        MessageContent(
            type=MessageType.OTP_VERIFICATION,
            recipient=MessageRecipient(phone=phone_e164, language=language),
            template_params={"code": code, "store_name": store_name},
        )
    )
    if not result.success:
        logger.warning(
            "identity_otp_send_failed",
            extra={
                "store_id": str(store_id),
                "error_code": result.error_code,
            },
        )
    return result.success


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/identity/otp/issue",
    response_model=SuccessResponse[IdentityOtpIssueResponse],
    summary="Issue a WhatsApp verification code for this cart session",
    operation_id="storefront_identity_otp_issue",
)
async def identity_otp_issue(
    store_id: Annotated[UUID, Path(description="Store ID")],
    body: IdentityOtpIssueRequest,
    cart_owner: Annotated[CartOwner, Depends(get_cart_owner)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    _require_store_match(cart_owner, store_id)
    phone_e164 = _canonical_phone(body.phone)

    salt = get_settings().platform_secret_salt
    phone_hash = extract_phone_hash_from_string(phone_e164)
    if not phone_hash or not salt:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "platform_salt_missing"},
        )

    store_row = (
        await session.execute(select(StoreModel).where(StoreModel.id == store_id))
    ).scalar_one_or_none()
    if store_row is None:
        raise HTTPException(status_code=404, detail={"code": "store_not_found"})

    # Capability first: don't burn rate-limit budget or write rows for a
    # store whose transport can't deliver anyway.
    if not await otp_available(store_id, store_row.settings, session):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "otp_unavailable"},
        )

    cache = RedisCacheService()
    try:
        await _issue_rate_limits(cache, store_id, phone_hash)

        code = generate_code()
        now = datetime.now(UTC)
        otp = OtpCodeModel(
            id=uuid4(),
            tenant_id=store_row.tenant_id,
            store_id=store_id,
            phone_hash=phone_hash,
            code_hash=hash_code(code, salt),
            language=body.language,
            expires_at=expires_at_for_now(now=now),
            attempts_left=OTP_MAX_ATTEMPTS,
        )
        session.add(otp)
        await session.flush()

        # Pre-OTP cart↔phone attach (the abandoned-cart capture decision):
        # typing a phone and bailing before the code is still a recoverable
        # cart. Server half — the storefront also fires trackCartState()
        # into abandoned_checkouts. Best-effort: never blocks the code.
        try:
            from src.infrastructure.repositories.cart_repository import (
                RedisCartRepository,
            )

            cart_repo = RedisCartRepository()
            owner_id, _ = cart_owner.cart_key
            cart = (
                await cart_repo.get_by_customer_id(owner_id, store_id)
                if not cart_owner.is_guest
                else await cart_repo.get_by_session_id(str(owner_id), store_id)
            )
            if cart is not None:
                cart.metadata["phone_e164"] = phone_e164
                await cart_repo.save(cart)
        except Exception:
            logger.exception("identity_cart_phone_attach_failed")

        sent_ok = await _send_otp(
            session,
            store_id=store_id,
            tenant_id=store_row.tenant_id,
            store_name=store_row.name,
            phone_e164=phone_e164,
            code=code,
            language=body.language,
        )
        if not sent_ok:
            otp.failed_send_at = datetime.now(UTC)
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "otp_send_failed"},
            )

        await session.commit()
        return SuccessResponse(
            data=IdentityOtpIssueResponse(
                otp_id=str(otp.id),
                expires_at=otp.expires_at,
                resend_after=OTP_RESEND_COOLDOWN_SECONDS,
            )
        )
    finally:
        await cache.close()


@router.post(
    "/identity/otp/verify",
    response_model=SuccessResponse[IdentityOtpVerifyResponse],
    summary="Verify a WhatsApp code and identify the customer",
    operation_id="storefront_identity_otp_verify",
)
async def identity_otp_verify(
    store_id: Annotated[UUID, Path(description="Store ID")],
    body: IdentityOtpVerifyRequest,
    response: Response,
    cart_owner: Annotated[CartOwner, Depends(get_cart_owner)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    _require_store_match(cart_owner, store_id)
    phone_e164 = _canonical_phone(body.phone)

    salt = get_settings().platform_secret_salt
    if not salt:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "platform_salt_missing"},
        )

    try:
        otp_uuid = UUID(body.otp_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_otp_id"},
        ) from exc

    otp = (
        await session.execute(
            select(OtpCodeModel).where(
                and_(
                    OtpCodeModel.id == otp_uuid,
                    OtpCodeModel.store_id == store_id,
                )
            )
        )
    ).scalar_one_or_none()

    # The row stores only the phone HASH; the resubmitted phone must hash to
    # it. A mismatch is indistinguishable from an unknown otp_id on purpose —
    # neither confirms anything about any phone.
    if otp is None or extract_phone_hash_from_string(phone_e164) != otp.phone_hash:
        return SuccessResponse(
            data=IdentityOtpVerifyResponse(
                verdict=OtpVerdict.UNKNOWN.value, attempts_left=0
            )
        )

    result = evaluate_verify(
        submitted_code=body.code,
        stored_hash=otp.code_hash,
        salt=salt,
        expires_at=otp.expires_at
        if otp.expires_at.tzinfo
        else otp.expires_at.replace(tzinfo=UTC),
        attempts_left=otp.attempts_left,
        verified_at=otp.verified_at,
    )

    if result.verdict == OtpVerdict.WRONG_CODE:
        otp.attempts_left = result.attempts_left
        await session.commit()

    if result.verdict != OtpVerdict.VERIFIED:
        return SuccessResponse(
            data=IdentityOtpVerifyResponse(
                verdict=result.verdict.value,
                attempts_left=result.attempts_left,
            )
        )

    # ── VERIFIED ──────────────────────────────────────────────────────
    now = datetime.now(UTC)
    if otp.verified_at is None:
        otp.verified_at = now
        await session.commit()
        # Positive trust signal — same event the Shopify OTP path emits.
        get_event_bus().publish(
            OtpVerifiedEvent(
                otp_id=otp.id,
                tenant_id=otp.tenant_id,
                store_id=otp.store_id,
                phone_hash=otp.phone_hash,
                verified_at=otp.verified_at,
            )
        )

    # Mark THIS cart session verified for THIS phone — what checkout
    # enforcement reads. Written before the customer branch so a failure
    # there can't leave a verified customer unable to check out.
    cache = RedisCacheService()
    try:
        await write_identity_flag(
            cache,
            store_id,
            _cart_key_str(cart_owner),
            phone_e164=phone_e164,
            otp_id=str(otp.id),
        )
    except Exception:
        logger.exception("identity_flag_write_failed")
    finally:
        await cache.close()

    # Known phone → this is the customer; log them in and hand back a
    # prefill snapshot. Revealing existence HERE is fine — they just proved
    # ownership of the phone.
    from src.infrastructure.repositories.customer_repository import (
        CustomerRepository,
    )

    customer = await CustomerRepository(session).get_by_phone(store_id, phone_e164)
    customer_known = customer is not None
    profile: IdentityProfile | None = None

    if customer is not None:
        await session.execute(
            update(CustomerModel)
            .where(CustomerModel.id == customer.id)
            .values(phone_verified_at=now)
        )
        await session.commit()

        try:
            from src.infrastructure.external_services.token_service import (
                TokenService,
            )

            token_service = TokenService()
            set_customer_auth_cookies(
                response,
                token_service.create_customer_access_token(customer),
                token_service.create_customer_refresh_token(customer),
            )
        except Exception:
            # Cookies are a convenience (prefill via /me); the verify proof
            # itself is the Redis flag, which is already written.
            logger.exception("identity_customer_login_failed")

        # Merge the anonymous cart into the customer's cart, mirroring the
        # login flow, so the badge count and checkout see one cart.
        if cart_owner.is_guest and cart_owner.session_id is not None:
            try:
                from src.infrastructure.repositories.cart_repository import (
                    RedisCartRepository,
                )

                await RedisCartRepository().transfer_to_customer(
                    str(cart_owner.session_id), customer.id, store_id
                )
            except Exception:
                logger.exception("identity_cart_transfer_failed")

        email_str = str(customer.email) if customer.email else None
        if email_str and email_str.endswith(_GUEST_EMAIL_DOMAIN):
            email_str = None
        profile = IdentityProfile(
            first_name=customer.first_name,
            last_name=customer.last_name,
            email=email_str,
            phone=phone_e164,
        )

    logger.info(
        "identity_otp_verified",
        extra={
            "store_id": str(store_id),
            "customer_known": customer_known,
        },
    )
    return SuccessResponse(
        data=IdentityOtpVerifyResponse(
            verdict=OtpVerdict.VERIFIED.value,
            attempts_left=result.attempts_left,
            customer_known=customer_known,
            profile=profile,
        )
    )


@router.get(
    "/identity/status",
    response_model=SuccessResponse[IdentityStatusResponse],
    summary="Identity gate state for this cart session",
    operation_id="storefront_identity_status",
)
async def identity_status(
    store_id: Annotated[UUID, Path(description="Store ID")],
    cart_owner: Annotated[CartOwner, Depends(get_cart_owner)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    _require_store_match(cart_owner, store_id)

    store_row = (
        await session.execute(select(StoreModel).where(StoreModel.id == store_id))
    ).scalar_one_or_none()
    if store_row is None:
        raise HTTPException(status_code=404, detail={"code": "store_not_found"})

    identity_cfg = resolve_config(store_row.settings)["identity"]
    available = await otp_available(store_id, store_row.settings, session)

    verified = False
    phone_masked: str | None = None

    # An authenticated customer whose phone is already OTP-proven doesn't
    # need the gate again on a new device/session.
    if cart_owner.customer_id is not None:
        row = (
            await session.execute(
                select(CustomerModel.phone, CustomerModel.phone_verified_at).where(
                    CustomerModel.id == cart_owner.customer_id
                )
            )
        ).first()
        if row and row[0] and row[1] is not None:
            verified = True
            phone_masked = _mask_phone(row[0])

    if not verified:
        cache = RedisCacheService()
        try:
            flag = await read_identity_flag(cache, store_id, _cart_key_str(cart_owner))
        except Exception:
            # Redis down → report unverified; the checkout enforcement has
            # its own fail-open, so this only costs a redundant OTP prompt.
            logger.exception("identity_status_flag_read_failed")
            flag = None
        finally:
            await cache.close()
        if flag and flag.get("phone"):
            verified = True
            phone_masked = _mask_phone(str(flag["phone"]))

    return SuccessResponse(
        data=IdentityStatusResponse(
            required=bool(identity_cfg.get("require_verification")) and available,
            otp_available=available,
            verified=verified,
            phone_masked=phone_masked,
        )
    )
