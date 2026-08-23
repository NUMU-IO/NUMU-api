"""Message routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_channel_connection_repository,
    get_channel_message_repository,
    get_message_thread_repository,
)
from src.api.responses import SuccessResponse
from src.application.dto.omnichannel import SendMessageBodyDTO
from src.application.use_cases.omnichannel import (
    ListMessagesUseCase,
    SendMessageUseCase,
)
from src.infrastructure.external_services.meta.graph_client import MetaGraphAPIError
from src.infrastructure.repositories import (
    ChannelConnectionRepositoryImpl,
    ChannelMessageRepositoryImpl,
    MessageThreadRepositoryImpl,
)

router = APIRouter(tags=["Omnichannel"])


@router.get("/", status_code=status.HTTP_200_OK)
async def list_messages(
    thread_id: UUID,
    cursor: str | None = Query(None, description="Pagination cursor"),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    message_repo: ChannelMessageRepositoryImpl = Depends(
        get_channel_message_repository
    ),
) -> SuccessResponse:
    """List messages in a thread.

    GET /stores/{store_id}/inbox/threads/{thread_id}/messages
    Query: ?cursor=&limit=50
    """
    use_case = ListMessagesUseCase(channel_message_repository=message_repo)
    result = await use_case.execute(
        thread_id=thread_id,
        cursor=cursor,
        limit=limit,
    )
    return SuccessResponse(
        data=result,
        message=None,
    )


@router.post("/send", status_code=status.HTTP_200_OK)
async def send_message(
    payload: SendMessageBodyDTO,
    store_id: UUID,
    thread_id: UUID,
    db: AsyncSession = Depends(get_db),
    message_repo: ChannelMessageRepositoryImpl = Depends(
        get_channel_message_repository
    ),
    connection_repo: ChannelConnectionRepositoryImpl = Depends(
        get_channel_connection_repository
    ),
    thread_repo: MessageThreadRepositoryImpl = Depends(get_message_thread_repository),
) -> SuccessResponse:
    """Send a message to a thread.

    POST /stores/{store_id}/threads/{thread_id}/messages/send
    Body: { "type", "text", ... } — thread id comes from the path.
    """
    use_case = SendMessageUseCase(
        channel_connection_repository=connection_repo,
        message_thread_repository=thread_repo,
        channel_message_repository=message_repo,
    )

    def _is_window_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "(#10)" in text or "allowed window" in text

    try:
        message = await use_case.execute(
            thread_id=thread_id,
            message=payload.text or "",
            attachment_type=payload.attachment_type,
            attachment_url=payload.attachment_url,
            template_name=payload.template_name,
            template_params=payload.template_params,
        )
    except MetaGraphAPIError as exc:
        # Meta blocks standard sends >24h after the customer's last message.
        # A merchant typing in the Inbox IS the human-agent case, so retry
        # once with the HUMAN_AGENT tag (7-day window; needs the Human Agent
        # permission approved on the Meta app). Attachments can't be tagged.
        if not _is_window_error(exc) or payload.attachment_url:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Send failed: {exc}",
            ) from exc
        try:
            message = await use_case.execute(
                thread_id=thread_id,
                message=payload.text or "",
                message_tag="HUMAN_AGENT",
            )
        except MetaGraphAPIError as exc2:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Meta blocked this message: more than 24 hours passed since "
                    "the customer's last message (and the app lacks the Human "
                    "Agent permission for late replies). Ask the customer to "
                    "message you first."
                ),
            ) from exc2
    return SuccessResponse(
        data=message,
        message="Message sent successfully",
    )


__all__ = ["router"]
