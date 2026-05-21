"""WhatsApp chat inbox routes — conversations, messages, send replies."""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_store
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.stores.whatsapp import (
    ConversationListResponse,
    ConversationSummary,
    ConversationUpdateRequest,
    MessageBubble,
    MessageListResponse,
    SendMessageRequest,
    UnreadCountResponse,
)
from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.message_log import MessageLogModel
from src.infrastructure.database.models.tenant.whatsapp_conversation import (
    WhatsAppConversationModel,
)
from src.infrastructure.repositories.whatsapp_conversation_repository import (
    WhatsAppConversationRepository,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/{store_id}/whatsapp/conversations")


def _conv_to_summary(c: WhatsAppConversationModel) -> ConversationSummary:
    now = datetime.now(UTC)
    window_open = bool(c.window_expires_at and c.window_expires_at > now)
    return ConversationSummary(
        id=c.id,
        customer_phone=c.customer_phone,
        customer_name=c.customer_name,
        customer_id=c.customer_id,
        last_message_preview=c.last_message_preview,
        last_message_at=c.last_message_at,
        last_message_direction=c.last_message_direction,
        unread_count=c.unread_count,
        status=c.status,
        assigned_to=c.assigned_to,
        window_open=window_open,
        window_expires_at=c.window_expires_at,
    )


@router.get(
    "",
    response_model=SuccessResponse[ConversationListResponse],
    summary="List conversations (inbox)",
    operation_id="list_whatsapp_conversations",
)
async def list_conversations(
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
    conv_status: str | None = Query(None, alias="status"),
    unread_only: bool = Query(False),
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    repo = WhatsAppConversationRepository(db)
    convs, total = await repo.list_by_store(
        store.id,
        status=conv_status,
        unread_only=unread_only,
        search=search,
        skip=skip,
        limit=limit,
    )
    unread_total = await repo.get_unread_count(store.id)
    return SuccessResponse(
        data=ConversationListResponse(
            conversations=[_conv_to_summary(c) for c in convs],
            total=total,
            unread_total=unread_total,
        ),
        message="Conversations retrieved",
    )


@router.get(
    "/unread-count",
    response_model=SuccessResponse[UnreadCountResponse],
    summary="Get unread conversation count",
    operation_id="get_whatsapp_unread_count",
)
async def get_unread_count(
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    repo = WhatsAppConversationRepository(db)
    count = await repo.get_unread_count(store.id)
    return SuccessResponse(
        data=UnreadCountResponse(unread_count=count),
        message="Unread count retrieved",
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=SuccessResponse[MessageListResponse],
    summary="Get messages for a conversation",
    operation_id="get_whatsapp_messages",
)
async def get_messages(
    conversation_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    repo = WhatsAppConversationRepository(db)
    conv = await repo.get_by_id(conversation_id)
    if not conv or conv.store_id != store.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Fetch messages from message_logs for this phone + store
    result = await db.execute(
        select(MessageLogModel)
        .where(
            MessageLogModel.store_id == store.id,
            MessageLogModel.phone == conv.customer_phone,
        )
        .order_by(MessageLogModel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    logs = result.scalars().all()

    from sqlalchemy import func

    total_result = await db.execute(
        select(func.count(MessageLogModel.id)).where(
            MessageLogModel.store_id == store.id,
            MessageLogModel.phone == conv.customer_phone,
        )
    )
    total = total_result.scalar() or 0

    now = datetime.now(UTC)
    window_open = bool(conv.window_expires_at and conv.window_expires_at > now)

    messages = [
        MessageBubble(
            id=log.id,
            message_id=log.message_id,
            direction=log.direction.value
            if hasattr(log.direction, "value")
            else str(log.direction),
            content=log.content,
            template_name=log.template_name,
            status=log.status.value
            if hasattr(log.status, "value")
            else str(log.status),
            created_at=log.created_at,
            metadata=log.metadata_,
        )
        for log in reversed(logs)  # oldest first for chat display
    ]

    return SuccessResponse(
        data=MessageListResponse(
            messages=messages,
            total=total,
            window_open=window_open,
            window_expires_at=conv.window_expires_at,
        ),
        message="Messages retrieved",
    )


@router.post(
    "/{conversation_id}/send",
    response_model=SuccessResponse[MessageBubble],
    summary="Send a message in a conversation",
    operation_id="send_whatsapp_message",
)
async def send_message(
    conversation_id: UUID,
    request: SendMessageRequest,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    """Send a reply. Freeform text within 24h window, template-only outside."""
    repo = WhatsAppConversationRepository(db)
    conv = await repo.get_by_id(conversation_id)
    if not conv or conv.store_id != store.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    now = datetime.now(UTC)
    window_open = bool(conv.window_expires_at and conv.window_expires_at > now)

    # Resolve WhatsApp service for this store
    from src.infrastructure.external_services.whatsapp import get_whatsapp_service

    wa_service = await get_whatsapp_service(store.id, db, store.tenant_id)

    result = None

    if request.text and not request.template_id:
        # Freeform text — requires open window
        if not window_open:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="24-hour customer service window has expired. Use a template message instead.",
            )
        result = await wa_service.send_text_message(conv.customer_phone, request.text)

    elif request.template_id:
        # Template message — works anytime
        from src.infrastructure.repositories.whatsapp_template_repository import (
            WhatsAppTemplateRepository,
        )

        tmpl_repo = WhatsAppTemplateRepository(db)
        tmpl = await tmpl_repo.get_by_id(request.template_id)
        if not tmpl or tmpl.store_id != store.id:
            raise HTTPException(status_code=404, detail="Template not found")
        if tmpl.status != "APPROVED":
            raise HTTPException(status_code=400, detail="Template not yet approved")

        from src.core.interfaces.services.messaging_service import (
            MessageContent,
            MessageRecipient,
            MessageType,
        )

        recipient = MessageRecipient(
            phone=conv.customer_phone,
            name=conv.customer_name or "",
        )
        content = MessageContent(
            type=MessageType.CUSTOM,
            recipient=recipient,
            template_params=request.template_params or {},
        )
        result = await wa_service.send_message(content)

    elif request.media_url:
        if not window_open:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="24-hour window expired. Use a template message.",
            )
        result = await wa_service.send_media_message(
            conv.customer_phone,
            request.media_url,
            caption=request.media_caption,
        )
    else:
        raise HTTPException(
            status_code=400, detail="Provide text, template_id, or media_url"
        )

    if not result or not result.success:
        error_msg = result.error_message if result else "Unknown error"
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to send message: {error_msg}",
        )

    # Log the outbound message
    from src.core.entities.message_log import (
        MessageDirection,
        MessageLog,
    )
    from src.core.entities.message_log import MessageStatus as LogStatus
    from src.infrastructure.repositories.message_log_repository import (
        MessageLogRepository,
    )

    log_repo = MessageLogRepository(db)
    log_entry = MessageLog(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        phone=wa_service._format_phone_number(conv.customer_phone),
        message_id=result.message_id or str(uuid4()),
        direction=MessageDirection.OUTBOUND,
        content=request.text or f"[template:{request.template_id}]",
        status=LogStatus.SENT,
    )
    saved = await log_repo.create(log_entry)

    # Update conversation
    await repo.upsert_on_message(
        store_id=store.id,
        tenant_id=store.tenant_id,
        phone=conv.customer_phone,
        name=conv.customer_name,
        message_preview=request.text or "[Template message]",
        direction="outbound",
    )

    return SuccessResponse(
        data=MessageBubble(
            id=saved.id,
            message_id=saved.message_id,
            direction="outbound",
            content=saved.content,
            status="sent",
            created_at=saved.created_at,
        ),
        message="Message sent",
    )


@router.patch(
    "/{conversation_id}",
    response_model=SuccessResponse[ConversationSummary],
    summary="Update conversation (mark read, archive, assign)",
    operation_id="update_whatsapp_conversation",
)
async def update_conversation(
    conversation_id: UUID,
    request: ConversationUpdateRequest,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    repo = WhatsAppConversationRepository(db)
    conv = await repo.get_by_id(conversation_id)
    if not conv or conv.store_id != store.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if request.mark_read:
        await repo.mark_read(conversation_id)
    if request.status:
        await repo.update_status(conversation_id, request.status)
    if request.assigned_to is not None:
        await repo.assign(conversation_id, request.assigned_to)

    # Re-fetch
    conv = await repo.get_by_id(conversation_id)
    return SuccessResponse(
        data=_conv_to_summary(conv),
        message="Conversation updated",
    )
