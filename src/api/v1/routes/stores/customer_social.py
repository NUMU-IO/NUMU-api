"""Customer ⇄ social-conversation surface (feature: social commerce loop).

Three endpoints that close the loop between the omnichannel inbox and
commerce:

- ``GET  /customers/{customer_id}/social-profiles`` — the conversations a
  merchant has explicitly linked to this customer (omnichannel threads +
  the WhatsApp-only inbox), with the counterpart's name/avatar. Linking
  itself stays a deliberate Inbox action — nothing here guesses.
- ``POST /customers/{customer_id}/avatar`` — adopt a linked thread's
  profile picture as the customer's photo. Meta CDN avatar URLs are
  signed and expire, so the image is downloaded and re-hosted on our
  storage; the raw URL is only stored as a fallback. Persisted in the
  customer's ``metadata`` (``extra_data``) — no schema change.
- ``POST /orders/{order_id}/send-payment-link`` — actually send the
  order summary + storefront ``/pay/{order_id}`` link into a linked
  conversation (Messenger / Instagram / WhatsApp) through the same
  outbound path the Inbox composer uses, and log it on the order
  timeline.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_channel_connection_repository,
    get_channel_message_repository,
    get_customer_repository,
    get_message_thread_repository,
    get_order_activity_repository,
    get_order_repository,
)
from src.api.dependencies.services import get_storage_service
from src.api.responses import SuccessResponse
from src.application.use_cases.omnichannel import SendMessageUseCase
from src.core.entities.order_activity import OrderActivity, OrderActivityKind
from src.core.entities.store import Store
from src.core.interfaces.services.storage_service import StorageBucket
from src.core.logging import get_logger
from src.infrastructure.database.models.public.omnichannel import MessageThreadModel
from src.infrastructure.database.models.tenant.whatsapp_conversation import (
    WhatsAppConversationModel,
)
from src.infrastructure.external_services.meta.graph_client import MetaGraphAPIError
from src.infrastructure.repositories import (
    ChannelConnectionRepositoryImpl,
    ChannelMessageRepositoryImpl,
    CustomerRepository,
    MessageThreadRepositoryImpl,
    OrderActivityRepository,
    OrderRepository,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/{store_id}", tags=["Customer Social"])

# ── Schemas ──────────────────────────────────────────────────────────


class SocialProfileResponse(BaseModel):
    """One linked conversation, shaped for the customer profile page."""

    kind: str  # "thread" (omnichannel) | "whatsapp" (WhatsApp-only inbox)
    id: str
    channel: str  # facebook | instagram | whatsapp
    name: str | None
    avatar_url: str | None
    phone: str | None
    last_message_at: str | None
    last_message_preview: str | None
    status: str | None
    # Where the hub should navigate to open the conversation.
    inbox_path: str


class SocialProfilesResponse(BaseModel):
    profiles: list[SocialProfileResponse]


class SetAvatarRequest(BaseModel):
    thread_id: UUID


class SetAvatarResponse(BaseModel):
    avatar_url: str


class SendPaymentLinkRequest(BaseModel):
    thread_id: UUID
    note: str | None = Field(default=None, max_length=300)


class SendPaymentLinkResponse(BaseModel):
    message_id: str
    channel: str
    pay_url: str


# ── Helpers ──────────────────────────────────────────────────────────


def _is_window_error(exc: Exception) -> bool:
    """Graph error #10 — "message is sent outside of allowed window"."""
    text = str(exc).lower()
    return (
        "(#10)" in text
        or "outside of allowed window" in text
        or "allowed window" in text
    )


AVATAR_MAX_BYTES = 3 * 1024 * 1024
AVATAR_TIMEOUT_S = 6.0


async def _rehost_avatar(url: str, customer_id: UUID, storage) -> str | None:
    """Download a (likely expiring) CDN avatar and pin it on our storage."""
    try:
        async with httpx.AsyncClient(
            timeout=AVATAR_TIMEOUT_S, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";")[0].strip()
            if not content_type.startswith("image/"):
                return None
            body = resp.content
            if not body or len(body) > AVATAR_MAX_BYTES:
                return None
        ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(
            content_type, "jpg"
        )
        uploaded = await storage.upload_file(
            file_content=body,
            filename=f"customer-{customer_id}.{ext}",
            content_type=content_type,
            bucket=StorageBucket.AVATARS,
            key=f"customers/{customer_id}/{uuid4().hex[:12]}.{ext}",
        )
        return uploaded.url
    except Exception:  # noqa: BLE001 — re-hosting is best-effort
        logger.warning("customer_avatar_rehost_failed", customer_id=str(customer_id))
        return None


def _thread_profile(t: MessageThreadModel) -> SocialProfileResponse:
    return SocialProfileResponse(
        kind="thread",
        id=str(t.id),
        channel=t.channel,
        name=t.participant_name,
        avatar_url=t.participant_avatar_url,
        phone=t.participant_phone_e164,
        last_message_at=t.last_message_at.isoformat() if t.last_message_at else None,
        last_message_preview=t.last_message_preview,
        status=t.status,
        inbox_path=f"/inbox/{t.id}",
    )


def _wa_profile(c: WhatsAppConversationModel) -> SocialProfileResponse:
    return SocialProfileResponse(
        kind="whatsapp",
        id=str(c.id),
        channel="whatsapp",
        name=c.customer_name,
        avatar_url=c.customer_profile_pic_url,
        phone=c.customer_phone,
        last_message_at=c.last_message_at.isoformat() if c.last_message_at else None,
        last_message_preview=c.last_message_preview,
        status=c.status,
        inbox_path=f"/whatsapp/inbox?conversation={c.id}",
    )


async def _linked_threads(
    db: AsyncSession, store_id: UUID, customer_id: UUID
) -> list[MessageThreadModel]:
    rows = await db.execute(
        select(MessageThreadModel)
        .where(
            MessageThreadModel.store_id == store_id,
            MessageThreadModel.customer_id == customer_id,
        )
        .order_by(MessageThreadModel.last_message_at.desc().nullslast())
    )
    return list(rows.scalars().all())


# ── Routes ───────────────────────────────────────────────────────────


@router.get(
    "/customers/{customer_id}/social-profiles",
    response_model=SuccessResponse[SocialProfilesResponse],
    summary="Conversations linked to this customer",
    operation_id="get_customer_social_profiles",
)
async def get_customer_social_profiles(
    customer_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    threads = await _linked_threads(db, store.id, customer_id)
    profiles = [_thread_profile(t) for t in threads]

    wa_rows = await db.execute(
        select(WhatsAppConversationModel)
        .where(
            WhatsAppConversationModel.store_id == store.id,
            WhatsAppConversationModel.customer_id == customer_id,
        )
        .order_by(WhatsAppConversationModel.last_message_at.desc().nullslast())
    )
    profiles.extend(_wa_profile(c) for c in wa_rows.scalars().all())

    return SuccessResponse(data=SocialProfilesResponse(profiles=profiles))


@router.post(
    "/customers/{customer_id}/avatar",
    response_model=SuccessResponse[SetAvatarResponse],
    summary="Adopt a linked conversation's profile picture as the customer photo",
    operation_id="set_customer_avatar_from_thread",
)
async def set_customer_avatar(
    customer_id: Annotated[UUID, Path()],
    request: SetAvatarRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    storage=Depends(get_storage_service),
):
    customer = await customer_repo.get_by_id(customer_id)
    if customer is None or customer.store_id != store.id:
        raise HTTPException(status_code=404, detail="Customer not found")

    # The avatar is taken from the THREAD server-side — the client never
    # supplies a URL, so this can't be used to attach arbitrary images.
    thread = (
        await db.execute(
            select(MessageThreadModel).where(
                MessageThreadModel.id == request.thread_id,
                MessageThreadModel.store_id == store.id,
            )
        )
    ).scalar_one_or_none()
    source_url: str | None = None
    if thread is not None:
        if thread.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conversation is not linked to this customer",
            )
        source_url = thread.participant_avatar_url
    else:
        wa = (
            await db.execute(
                select(WhatsAppConversationModel).where(
                    WhatsAppConversationModel.id == request.thread_id,
                    WhatsAppConversationModel.store_id == store.id,
                )
            )
        ).scalar_one_or_none()
        if wa is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if wa.customer_id != customer_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Conversation is not linked to this customer",
            )
        source_url = wa.customer_profile_pic_url

    if not source_url:
        raise HTTPException(
            status_code=422, detail="This conversation has no profile picture"
        )

    hosted = await _rehost_avatar(source_url, customer_id, storage)
    final_url = hosted or source_url

    meta = dict(customer.metadata or {})
    meta["avatar_url"] = final_url
    meta["avatar_source"] = {
        "thread_id": str(request.thread_id),
        "rehosted": hosted is not None,
        "set_at": datetime.now(UTC).isoformat(),
    }
    customer.metadata = meta
    await customer_repo.update(customer)

    return SuccessResponse(
        data=SetAvatarResponse(avatar_url=final_url),
        message="Customer photo updated",
    )


@router.post(
    "/orders/{order_id}/send-payment-link",
    response_model=SuccessResponse[SendPaymentLinkResponse],
    summary="Send the order's payment link into a linked conversation",
    operation_id="send_order_payment_link",
)
async def send_order_payment_link(
    order_id: Annotated[UUID, Path()],
    request: SendPaymentLinkRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    activity_repo: Annotated[
        OrderActivityRepository, Depends(get_order_activity_repository)
    ],
    thread_repo: Annotated[
        MessageThreadRepositoryImpl, Depends(get_message_thread_repository)
    ],
    connection_repo: Annotated[
        ChannelConnectionRepositoryImpl, Depends(get_channel_connection_repository)
    ],
    message_repo: Annotated[
        ChannelMessageRepositoryImpl, Depends(get_channel_message_repository)
    ],
):
    order = await order_repo.get_by_id(order_id)
    if order is None or order.store_id != store.id:
        raise HTTPException(status_code=404, detail="Order not found")
    if str(order.payment_status).lower() in ("paid", "refunded"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order is already paid",
        )

    thread = (
        await db.execute(
            select(MessageThreadModel).where(
                MessageThreadModel.id == request.thread_id,
                MessageThreadModel.store_id == store.id,
            )
        )
    ).scalar_one_or_none()
    if thread is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # A payment request must only go to the person who owns the order.
    if thread.customer_id is None or thread.customer_id != order.customer_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation is not linked to this order's customer",
        )

    # Storefront /pay/{order_id} — the same public page the COD-recovery
    # WhatsApp button deep-links to (Paymob inline + Kashier redirect).
    store_url = (store.store_url or "").rstrip("/")
    if not store_url:
        raise HTTPException(status_code=422, detail="Store has no storefront URL")
    pay_url = f"{store_url}/pay/{order.id}"

    # Compose — Arabic-first (matches the customer base), PII-light, and
    # short enough for Instagram's 1000-char cap even with long products.
    lines = [
        f"أهلًا {thread.participant_name or ''}".strip() + " 👋",
        f"ده ملخص طلبك رقم {order.order_number} من {store.name}:",
    ]
    for li in list(order.line_items)[:3]:
        lines.append(f"• {li.product_name} ×{li.quantity}")
    remaining = len(order.line_items) - 3
    if remaining > 0:
        lines.append(f"… و{remaining} منتج كمان")
    total = f"{order.total / 100:.2f} {order.currency}"
    lines.append(f"الإجمالي: {total}")
    if request.note:
        lines.append(request.note.strip())
    lines.append("تقدر تدفع أونلاين بأمان من هنا:")
    lines.append(pay_url)
    text = "\n".join(line for line in lines if line)

    use_case = SendMessageUseCase(
        channel_connection_repository=connection_repo,
        message_thread_repository=thread_repo,
        channel_message_repository=message_repo,
    )
    # Meta only allows standard messages within 24h of the customer's last
    # message. A payment request for their own order is exactly what the
    # MESSAGE_TAG escape hatch is for, so on a window rejection we retry
    # tagged: Messenger → POST_PURCHASE_UPDATE (order/payment updates),
    # Instagram → HUMAN_AGENT (its only tag; 7-day window, needs the Human
    # Agent permission approved on the Meta app). If Meta still refuses,
    # the merchant gets an actionable 409 — not a blank 500.
    window_tag = {
        "facebook": "POST_PURCHASE_UPDATE",
        "instagram": "HUMAN_AGENT",
    }.get(thread.channel)
    try:
        sent = await use_case.execute(thread_id=thread.id, message=text)
    except MetaGraphAPIError as exc:
        if window_tag is None or not _is_window_error(exc):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"{thread.channel} send failed: {exc}",
            ) from exc
        logger.info(
            "payment_link_window_retry",
            thread_id=str(thread.id),
            channel=thread.channel,
            tag=window_tag,
        )
        try:
            sent = await use_case.execute(
                thread_id=thread.id, message=text, message_tag=window_tag
            )
        except MetaGraphAPIError as exc2:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Meta blocked the send: more than 24 hours passed since the "
                    "customer's last message on this channel. Ask them to send "
                    "any message first, or use another linked channel."
                ),
            ) from exc2

    channel_label = {
        "facebook": "Messenger",
        "instagram": "Instagram",
        "whatsapp": "WhatsApp",
    }.get(thread.channel, thread.channel)
    await activity_repo.create(
        OrderActivity(
            order_id=order.id,
            store_id=store.id,
            tenant_id=order.tenant_id,
            user_id=store.owner_id,
            kind=OrderActivityKind.SYSTEM_EVENT,
            event_type="payment_link_sent",
            body=f"Payment link sent via {channel_label}",
            metadata={
                "thread_id": str(thread.id),
                "channel": thread.channel,
                "pay_url": pay_url,
            },
        )
    )

    return SuccessResponse(
        data=SendPaymentLinkResponse(
            message_id=str(sent.id),
            channel=thread.channel,
            pay_url=pay_url,
        ),
        message="Payment link sent",
    )


__all__ = ["router"]
