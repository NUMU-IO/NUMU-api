"""Storefront-facing WhatsApp opt-in endpoint (FR-007 + FR-007a).

Anonymous (no bearer auth), but gated by a valid checkout-session token
issued by ``POST /storefront/{store_slug}/checkout-session`` (FR-007b).

The token binds the caller's cart session to a specific phone at the
checkout Contact step. On opt-in submission, the handler verifies:
1. The supplied ``checkout_session_token`` resolves in Redis.
2. The session's ``store_id`` matches the addressed store_slug's store.
3. The session is not expired.
4. The phone in the request body, after E.164 canonicalization, matches
   the phone stored on the session.

This closes the storefront-opt-in abuse vector from analyze finding A1
(anyone with a store slug writing arbitrary opt-in rows).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from src.api.dependencies.repositories import get_store_repository
from src.api.v1.schemas.stores.whatsapp_opt_in import OptInRow, StorefrontOptIn
from src.application.use_cases.whatsapp.opt_in_customer import OptInCustomerUseCase
from src.core.value_objects.phone import InvalidPhoneError, PhoneNumber
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories.checkout_session_repository import (
    CheckoutSessionRepository,
)
from src.infrastructure.repositories.store_repository import StoreRepository

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_checkout_session_repository() -> CheckoutSessionRepository:
    return CheckoutSessionRepository()


@router.post(
    "/storefront/{store_slug}/whatsapp/opt-in",
    status_code=status.HTTP_201_CREATED,
    response_model=OptInRow,
    tags=["Storefront - WhatsApp"],
)
async def storefront_opt_in(
    body: StorefrontOptIn,
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    session_repo: Annotated[
        CheckoutSessionRepository, Depends(_get_checkout_session_repository)
    ],
    store_slug: str = Path(..., min_length=1),
) -> OptInRow:
    """Storefront-facing opt-in. Gated by checkout-session token (FR-007a).

    Idempotent: if an active opt-in already exists for (store, phone)
    the existing row is returned with 201 (no duplicate).
    """
    # 1. Resolve the store by slug.
    store = await store_repo.get_by_subdomain(store_slug)
    if store is None:
        # Custom-domain fallback — same lookup the cart-owner dep uses.
        try:
            store = await store_repo.get_by_custom_domain(store_slug)
        except Exception:
            store = None
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "store_not_found"},
        )

    # 2. Resolve + validate the checkout-session token.
    #    Missing / expired / wrong store → 403 invalid_checkout_session.
    session = await session_repo.get(body.checkout_session_token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "invalid_checkout_session",
                "message": (
                    "Checkout-session token is missing, expired, or has been used."
                ),
            },
        )
    if session.store_id != store.id:
        # Token issued for a different store. Same code as missing — do
        # NOT leak that the token exists for a different store.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "invalid_checkout_session",
                "message": (
                    "Checkout-session token is missing, expired, or has been used."
                ),
            },
        )

    # 3. Canonicalize the request body's phone to E.164 and compare to
    #    the session's stored phone (also E.164 at issue time).
    try:
        phone_e164 = PhoneNumber.parse(body.phone.strip(), default_region="EG").e164
    except InvalidPhoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_phone", "message": str(exc)},
        ) from exc

    if phone_e164 != session.phone:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "phone_mismatch_with_cart",
                "message": (
                    "The phone in the request does not match the phone on the "
                    "active checkout session."
                ),
            },
        )

    # 4. Write the opt-in row via the use-case. Uses a fresh DB session
    #    because this storefront route doesn't carry the tenant-scoped DI
    #    chain that merchant routes use.
    async with AsyncSessionLocal() as db:
        # Set RLS context for this store's tenant so the use-case's store
        # lookup succeeds and the insert lands under the right tenant.
        from src.infrastructure.tenancy.rls import RLSContext

        async with RLSContext(db, store.tenant_id):
            use_case = OptInCustomerUseCase(db)
            try:
                row = await use_case.execute(
                    store_id=store.id,
                    phone=phone_e164,
                    source="checkout",
                    customer_id=body.customer_id_hint,
                )
            except InvalidPhoneError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "invalid_phone", "message": str(exc)},
                ) from exc
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "invalid_request", "message": str(exc)},
                ) from exc
            await db.commit()

        # Snapshot row to a transport DTO before the session closes —
        # otherwise model_validate of an expired ORM object on close
        # would raise.
        row_data = OptInRow.model_validate(row)

    logger.info(
        "whatsapp_storefront_opt_in",
        store_id=str(store.id),
        phone_tail=phone_e164[-4:],
        opt_in_id=str(row_data.id),
    )

    # One-shot token: invalidate after use so a leaked token can't be
    # reused. Re-opting later requires the storefront to issue a fresh
    # checkout-session token.
    try:
        await session_repo.delete(body.checkout_session_token)
    except Exception as exc:
        logger.warning(
            "checkout_session_delete_failed_post_opt_in",
            error=str(exc),
        )

    return row_data
