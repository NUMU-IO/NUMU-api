"""Abandoned-checkout management routes nested under stores.

URL: /stores/{store_id}/abandoned-checkouts

Backs the merchant hub's Abandoned Checkouts page. The data itself comes
from a separate `abandoned_checkouts` table — populated by the storefront
checkout flow + a background job that flips `abandoned_at` once a row
sits inactive past the threshold. Both writers live outside this router;
here we only expose merchant-facing read + recovery actions.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_abandoned_checkout_repository,
    verify_store_ownership,
)
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.abandoned_checkout import (
    AbandonedCheckoutListResponse,
    AbandonedCheckoutResponse,
    SendRecoveryEmailResponse,
)
from src.core.entities.abandoned_checkout import AbandonedCheckout
from src.core.entities.store import Store
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories import AbandonedCheckoutRepository


class NotifyWhatsAppResponse(BaseModel):
    """Result of a merchant-initiated abandoned-cart WhatsApp nudge."""

    sent: bool
    # Machine-readable skip reason when sent is False so the merchant hub
    # can show a precise toast (no_phone, opt_out, no_credentials,
    # template_not_approved, already_notified_recently, send_failed, …).
    reason: str | None = None
    message_id: str | None = None


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/abandoned-checkouts")


def _cart_started_at(c: AbandonedCheckout) -> datetime | None:
    """When the CURRENT cart session began, if we know.

    Stamped by ``cart/track`` each time the session fingerprint changes.
    Rows written before that shipped have no value and fall back to
    ``created_at`` in the hub.
    """
    raw = (c.extra_data or {}).get("cart_started_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _to_response(c: AbandonedCheckout) -> AbandonedCheckoutResponse:
    return AbandonedCheckoutResponse(
        id=c.id,
        store_id=c.store_id,
        customer_id=c.customer_id,
        email=c.email,
        phone=c.phone,
        line_items=c.line_items,  # validated by AbandonedCheckoutLineItem
        shipping_address=c.shipping_address,
        subtotal=c.subtotal,
        shipping_cost=c.shipping_cost,
        tax_amount=c.tax_amount,
        discount_amount=c.discount_amount,
        total=c.total,
        currency=c.currency,
        coupon_code=c.coupon_code,
        utm_source=c.utm_source,
        utm_medium=c.utm_medium,
        utm_campaign=c.utm_campaign,
        last_activity_at=c.last_activity_at,
        abandoned_at=c.abandoned_at,
        recovered_at=c.recovered_at,
        recovery_email_sent_at=c.recovery_email_sent_at,
        recovered_order_id=c.recovered_order_id,
        item_count=sum((li.get("quantity") or 0) for li in c.line_items),
        created_at=c.created_at,
        updated_at=c.updated_at,
        cart_started_at=_cart_started_at(c),
    )


class AbandonedCheckoutSummaryResponse(BaseModel):
    """Analytics strip on the hub's Abandoned carts page."""

    open_count: int
    open_value_cents: int
    recovered_count: int
    recovered_value_cents: int
    reminders_sent: int
    payback_pct: float
    currency: str


@router.get(
    "/summary",
    response_model=SuccessResponse[AbandonedCheckoutSummaryResponse],
    summary="Abandoned vs recovered carts — counts, value, payback",
    operation_id="get_abandoned_checkout_summary",
)
async def get_abandoned_checkout_summary(
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
):
    data = await repo.summary(store.id, date_from=date_from, date_to=date_to)
    return SuccessResponse(
        data=AbandonedCheckoutSummaryResponse(
            **data, currency=getattr(store, "default_currency", None) or "EGP"
        )
    )


@router.get(
    "/",
    response_model=SuccessResponse[AbandonedCheckoutListResponse],
    summary="List abandoned checkouts for a store",
    operation_id="list_abandoned_checkouts",
)
async def list_abandoned_checkouts(
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    include_recovered: bool = Query(
        False,
        description="Include carts that have already been recovered.",
    ),
    only_recovered: bool = Query(
        False,
        description="Show only recovered carts (e.g. for analytics views).",
    ),
    abandonment_threshold_minutes: int = Query(
        60,
        ge=5,
        le=2880,
        description=(
            "Carts inactive for this many minutes are eligible to be "
            "flipped to `abandoned`. Lazily applied on each list-page load."
        ),
    ),
    has_contact: bool | None = Query(
        None,
        description=(
            "True = only carts with email or phone (recoverable, Shopify "
            "semantics). False = only carts with no contact info. "
            "Omit for everything."
        ),
    ),
):
    """Return the paginated abandoned-checkout feed for the store."""
    if only_recovered:
        recovered_filter: bool | None = True
    elif include_recovered:
        recovered_filter = None
    else:
        recovered_filter = False

    # Lazy abandonment: bulk-flip stale rows before reading. The threshold
    # tells the merchant which rows are *definitely* abandoned; rows newer
    # than the threshold show as "in progress" via the absence of an
    # abandoned_at timestamp. Best-effort; ignore failures.
    if not only_recovered:
        try:
            await repo.mark_stale_as_abandoned(
                store.id, threshold_seconds=abandonment_threshold_minutes * 60
            )
        except Exception:
            pass

    # Show ALL non-recovered carts (both abandoned_at-flagged and still
    # in-progress) by default. The merchant wants to see carts they can
    # potentially recover — waiting an hour for the threshold to flip
    # them visible is a confusing dead zone. The row's `abandoned_at`
    # timestamp on the response lets the UI badge each row's state.
    skip = (page - 1) * limit
    items, total = await repo.list_by_store(
        store_id=store.id,
        skip=skip,
        limit=limit,
        abandoned_only=False,
        recovered_only=recovered_filter,
        has_contact=has_contact,
    )

    total_pages = (total + limit - 1) // limit if total > 0 else 0

    return SuccessResponse(
        data=AbandonedCheckoutListResponse(
            items=[_to_response(c) for c in items],
            total=total,
            page=page,
            page_size=limit,
            total_pages=total_pages,
        ),
        message="Abandoned checkouts retrieved successfully",
    )


@router.get(
    "/{checkout_id}",
    response_model=SuccessResponse[AbandonedCheckoutResponse],
    summary="Get an abandoned checkout",
    operation_id="get_abandoned_checkout",
)
async def get_abandoned_checkout(
    checkout_id: Annotated[UUID, Path(description="Abandoned-checkout ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
):
    checkout = await repo.get_by_id(checkout_id)
    if not checkout or checkout.store_id != store.id:
        raise EntityNotFoundError("AbandonedCheckout", str(checkout_id))
    return SuccessResponse(
        data=_to_response(checkout),
        message="Abandoned checkout retrieved successfully",
    )


# Deliberately permissive: this only needs to catch the values that are clearly
# NOT addresses (phone numbers, names, placeholders) before we hand them to the
# provider. Real address validation belongs to the provider, not a regex.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Resend documents two acceptable shapes: "email@example.com" and
# "Name <email@example.com>". Accept both, so a stored display-name form is not
# rejected by us and then happily accepted by the provider.
_NAMED_EMAIL_RE = re.compile(r"^.*<\s*[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+\s*>$")


def _looks_like_email(value: str | None) -> bool:
    v = (value or "").strip()
    return bool(_EMAIL_RE.match(v) or _NAMED_EMAIL_RE.match(v))


@router.post(
    "/{checkout_id}/send-recovery-email",
    response_model=SuccessResponse[SendRecoveryEmailResponse],
    summary="Send a recovery email to the abandoned-checkout's customer",
    operation_id="send_recovery_email",
)
async def send_recovery_email(
    checkout_id: Annotated[UUID, Path(description="Abandoned-checkout ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
):
    """Send a recovery email and stamp `recovery_email_sent_at`.

    The email subject + body are rendered from a future template; for now
    we fall back to the existing Resend service with a minimal HTML body.
    Idempotent — calling twice simply overwrites the timestamp.
    """
    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.external_services.resend.email_templates.abandoned_cart import (  # noqa: E501
        abandoned_cart_email_html,
    )

    checkout = await repo.get_by_id(checkout_id)
    if not checkout or checkout.store_id != store.id:
        raise EntityNotFoundError("AbandonedCheckout", str(checkout_id))

    if not checkout.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This checkout has no email address — recovery email cannot be sent",
        )

    # Guest checkouts do not always put an EMAIL in the email column — a phone
    # number or a placeholder shows up often enough. Resend rejects those, and
    # the merchant saw an opaque 502 from the provider on a button they pressed.
    # Checking the shape here turns that into an answerable message.
    if not _looks_like_email(checkout.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{checkout.email}' is not a valid email address, so no "
                "recovery email can be sent. Try the WhatsApp nudge instead."
            ),
        )

    if checkout.recovered_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This checkout has already been recovered",
        )

    # The link back to the cart. `/api/cart/recover` rebuilds the shopper's
    # session from this checkout and lands them on /cart with the items
    # restored — the same route the WhatsApp nudge reaches through the apex
    # redirector, taken directly here because an email has no reason to make
    # an extra hop. Built from `store_url`, so a store on a custom domain
    # links to its own domain rather than to numueg.app.
    #
    # Without this the email asked the shopper to come back and gave them no
    # way to do it.
    recovery_url = f"{store.store_url.rstrip('/')}/api/cart/recover?cart={checkout.id}"

    subject, html = abandoned_cart_email_html(
        store_name=store.name,
        recovery_url=recovery_url,
        line_items=list(checkout.line_items or []),
        total_cents=checkout.total,
        currency=checkout.currency or "EGP",
        customer_name=_recipient_name_from_checkout(checkout),
        # The shopper's language, not the merchant's dashboard language.
        language=(store.default_language or "ar"),
        logo_url=store.logo_url,
    )

    service = ResendEmailService()
    await service.send_email(
        EmailMessage(to=str(checkout.email), subject=subject, html_content=html)
    )

    now = datetime.now(UTC)
    updated = await repo.mark_recovery_email_sent(checkout_id, now)

    return SuccessResponse(
        data=SendRecoveryEmailResponse(
            checkout_id=updated.id,
            email=updated.email or "",
            sent_at=now,
        ),
        message="Recovery email sent",
    )


@router.post(
    "/{checkout_id}/mark-recovered",
    response_model=SuccessResponse[AbandonedCheckoutResponse],
    summary="Manually mark an abandoned checkout as recovered",
    operation_id="mark_abandoned_checkout_recovered",
)
async def mark_abandoned_checkout_recovered(
    checkout_id: Annotated[UUID, Path(description="Abandoned-checkout ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
    order_id: UUID | None = Query(
        None,
        description="Order ID the checkout was recovered into, if known.",
    ),
):
    """Manually flag a cart as recovered. Used for off-channel conversions
    (merchant called the customer and they ordered via WhatsApp instead)."""
    checkout = await repo.get_by_id(checkout_id)
    if not checkout or checkout.store_id != store.id:
        raise EntityNotFoundError("AbandonedCheckout", str(checkout_id))

    updated = await repo.mark_recovered(checkout_id, order_id=order_id)
    return SuccessResponse(
        data=_to_response(updated),
        message="Checkout marked as recovered",
    )


def _recipient_name_from_checkout(c: AbandonedCheckout) -> str | None:
    """Best-effort customer display name from the shipping-address sketch."""
    addr = c.shipping_address or {}
    name = f"{addr.get('first_name', '')} {addr.get('last_name', '')}".strip()
    return name or None


@router.post(
    "/{checkout_id}/notify-whatsapp",
    response_model=NotifyWhatsAppResponse,
    summary="Send a WhatsApp abandoned-cart recovery nudge",
    operation_id="notify_abandoned_checkout_whatsapp",
)
async def notify_whatsapp(
    checkout_id: Annotated[UUID, Path(description="Abandoned-checkout ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    repo: Annotated[
        AbandonedCheckoutRepository, Depends(get_abandoned_checkout_repository)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NotifyWhatsAppResponse:
    """Merchant-initiated WhatsApp nudge for one abandoned checkout.

    This is the per-row "Notify via WhatsApp" action. Because the merchant
    explicitly chose to message this customer, it BYPASSES the marketing
    opt-in requirement (and per product decision, opt-out too) — it still
    respects store credentials and the ``abandoned_cart_v2`` template's
    APPROVED status so we never ship a malformed send to Meta.
    """
    from sqlalchemy import select

    from src.core.enums.whatsapp import TemplateCategory
    from src.core.interfaces.services.messaging_service import MessageRecipient
    from src.core.services.whatsapp_send_guard import GuardContext, check
    from src.infrastructure.database.models.tenant.whatsapp_template import (
        WhatsAppTemplateModel,
    )
    from src.infrastructure.external_services.whatsapp import (
        get_whatsapp_service,
        requires_template_approval,
    )

    checkout = await repo.get_by_id(checkout_id)
    if not checkout or checkout.store_id != store.id:
        raise EntityNotFoundError("AbandonedCheckout", str(checkout_id))

    if not checkout.phone:
        return NotifyWhatsAppResponse(sent=False, reason="no_phone")

    if checkout.recovered_at is not None:
        return NotifyWhatsAppResponse(sent=False, reason="already_recovered")

    phone = checkout.phone

    # Per-customer cooldown — shares the 24h key the scheduled abandoned-cart
    # task sets. abandoned_cart_v2 is a MARKETING template; Meta frequency-caps
    # marketing sends per recipient and silently drops repeats with error
    # 131049 ("not delivered to maintain healthy ecosystem engagement"). So
    # nudging several of one customer's carts would deliver only the first.
    # Gate the manual action on the same key so the merchant gets a clear
    # "already nudged this customer" response instead of firing a send Meta
    # will drop. Keyed by customer_id when known (shares the auto-task key),
    # else by phone for guest checkouts (manual-to-manual dedup).
    from src.config import settings as _settings
    from src.infrastructure.cache.redis_cache import RedisCacheService
    from src.infrastructure.messaging.tasks.abandoned_cart_tasks import (
        NOTIFICATION_COOLDOWN_KEY,
        NOTIFICATION_COOLDOWN_SECONDS,
    )

    if checkout.customer_id is not None:
        cooldown_key = NOTIFICATION_COOLDOWN_KEY.format(
            store_id=store.id, customer_id=checkout.customer_id
        )
    else:
        cooldown_key = f"abandoned_cart_notified:{store.id}:phone:{phone}"

    if _settings.redis_host:
        _cache = RedisCacheService()
        try:
            if await _cache.exists(cooldown_key):
                logger.info(
                    "abandoned_cart_whatsapp_cooldown store=%s checkout=%s",
                    store.id,
                    checkout_id,
                )
                return NotifyWhatsAppResponse(
                    sent=False, reason="already_notified_recently"
                )
        finally:
            await _cache.close()

    # abandoned_cart_v2 is seeded for {"en", "ar"} (NOT en_US). Resolve the
    # send language to one of those so both the DB template-status lookup
    # and the messaging service's EGYPTIAN_TEMPLATES key match.
    #
    # Honor the store-level message-language override (the "send in which
    # language" control on the WhatsApp Overview page, persisted at
    # store.settings.whatsapp.message_language) exactly like the
    # order-lifecycle path in whatsapp_notification_handler. "ar"/"en" force
    # that language; "auto" (default) follows store.default_language. Without
    # this, a merchant who set Arabic still got English here because we only
    # looked at default_language.
    wa_lang_pref = str(
        ((store.settings or {}).get("whatsapp") or {}).get("message_language") or "auto"
    ).lower()
    if wa_lang_pref == "ar":
        raw_lang = "ar"
    elif wa_lang_pref == "en":
        raw_lang = "en"
    else:  # auto
        raw_lang = (store.default_language or "ar").lower()
    language = "en" if raw_lang.startswith("en") else "ar"

    # Template approval status (FR-029) — guards against a 400 from Meta.
    tmpl_row = (
        await db.execute(
            select(WhatsAppTemplateModel).where(
                WhatsAppTemplateModel.store_id == store.id,
                WhatsAppTemplateModel.name == "abandoned_cart_v2",
                WhatsAppTemplateModel.language == language,
            )
        )
    ).scalar_one_or_none()
    template_status = tmpl_row.status if tmpl_row is not None else None

    store_settings = store.settings or {}
    credential_error = bool(
        (store_settings.get("whatsapp") or {}).get("credential_error")
    )

    ctx = GuardContext(
        phone=phone,
        template_name="abandoned_cart_v2",
        template_category=TemplateCategory.MARKETING,
        template_status=getattr(template_status, "value", template_status),
        store_has_credentials=True,  # resolver always yields a service
        store_credentials_marked_invalid=credential_error,
        # Merchant explicitly clicked Notify — the per-message toggle does
        # not gate the manual action.
        notification_setting_enabled=True,
        # Product decision: manual + auto abandoned-cart sends bypass the
        # marketing opt-in AND opt-out gates (entering a phone at checkout
        # is treated as implied consent). Still respects creds + approval.
        has_active_opt_in=True,
        has_opt_out=False,
        window_is_open=True,  # template send ignores the 24h window
        already_sent=False,  # merchant may deliberately re-notify
        # A store on GOWA sends the rendered text, not a template reference,
        # so Meta's review state cannot block this send — and abandoned_cart
        # is precisely the message merchants move to GOWA to escape (Meta's
        # 131049 marketing cap delivers only the first nudge).
        requires_template_approval=requires_template_approval(store_settings),
    )
    decision = check(ctx)
    if not decision.allowed:
        reason = decision.reason.value if decision.reason else "blocked"
        logger.info(
            "abandoned_cart_whatsapp_skipped store=%s checkout=%s reason=%s",
            store.id,
            checkout_id,
            reason,
        )
        return NotifyWhatsAppResponse(sent=False, reason=reason)

    service = await get_whatsapp_service(store.id, db, store.tenant_id)
    recipient = MessageRecipient(
        phone=phone,
        name=_recipient_name_from_checkout(checkout),
        language=language,
    )
    # cart_token button param: `<subdomain>/<checkout_id>`. The apex
    # /cart/<subdomain>/<id> redirector forwards to the storefront's
    # /api/cart/recover route, which rebuilds this exact abandoned cart
    # into the shopper's session and drops them on /cart with the items
    # restored (instead of a generic, empty cart page).
    result = await service.send_abandoned_cart(
        recipient,
        store.name,
        cart_token=(f"{store.subdomain}/{checkout_id}" if store.subdomain else ""),
    )

    if result.success:
        # Set the per-customer cooldown (same key the scheduled task uses) so
        # further manual nudges to any of this customer's carts are
        # short-circuited for 24h instead of hitting Meta's frequency cap.
        if _settings.redis_host:
            _cache = RedisCacheService()
            try:
                await _cache.set(
                    cooldown_key, "1", expire=NOTIFICATION_COOLDOWN_SECONDS
                )
            finally:
                await _cache.close()
        # Stamp the row so the UI / cooldown logic can see it was nudged.
        checkout.extra_data = {
            **(checkout.extra_data or {}),
            "last_whatsapp_notified_at": datetime.now(UTC).isoformat(),
        }
        try:
            await repo.update(checkout)
        except Exception:
            logger.warning(
                "abandoned_cart_whatsapp_stamp_failed checkout=%s", checkout_id
            )

        # Persist an OUTBOUND message_log so this manual nudge shows up in the
        # WhatsApp dashboard's Sent count + Recent-messages feed (both read
        # message_logs filtered by store_id). The order-lifecycle path does
        # this via _persist_message_log; the manual abandoned-cart path
        # previously skipped it, so successful nudges were invisible in the UI.
        # Audit-only — never let a logging failure fail the send.
        if result.message_id:
            try:
                from src.core.entities.message_log import (
                    MessageDirection,
                    MessageLog,
                    MessageStatus,
                )
                from src.infrastructure.repositories.message_log_repository import (
                    MessageLogRepository,
                )

                await MessageLogRepository(db).create(
                    MessageLog(
                        tenant_id=store.tenant_id,
                        store_id=store.id,
                        phone=phone,
                        metadata={"checkout_id": str(checkout_id)},
                        message_id=result.message_id,
                        direction=MessageDirection.OUTBOUND,
                        template_name="abandoned_cart_v2",
                        status=MessageStatus.SENT,
                    )
                )
                await db.commit()
            except Exception:
                logger.warning(
                    "abandoned_cart_whatsapp_log_failed checkout=%s", checkout_id
                )

        return NotifyWhatsAppResponse(sent=True, message_id=result.message_id)

    logger.warning(
        "abandoned_cart_whatsapp_send_failed store=%s checkout=%s error=%s",
        store.id,
        checkout_id,
        result.error_message,
    )
    return NotifyWhatsAppResponse(sent=False, reason="send_failed")
