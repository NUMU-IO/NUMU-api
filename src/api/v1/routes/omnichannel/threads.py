"""Message thread routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_message_thread_repository,
)
from src.api.responses import SuccessResponse
from src.application.use_cases.omnichannel import (
    GetThreadUseCase,
    ListThreadsUseCase,
    MarkThreadReadUseCase,
    ResolveThreadUseCase,
)
from src.infrastructure.repositories import MessageThreadRepositoryImpl

router = APIRouter(tags=["Omnichannel"])


@router.get("/", response_model=dict, status_code=status.HTTP_200_OK)
async def list_threads(
    store_id: UUID,
    channel: str | None = Query(
        None, description="Filter by channel (facebook|instagram|whatsapp)"
    ),
    status_filter: str | None = Query(
        None, description="Filter by status (open|resolved|spam)"
    ),
    unread_only: bool = Query(False, description="Only threads with unread"),
    search: str | None = Query(None, description="Search participant name/phone"),
    cursor: str | None = Query(None, description="Pagination cursor"),
    limit: int = Query(50, ge=1, le=100, description="Max results"),
    db: AsyncSession = Depends(get_db),
    thread_repo: MessageThreadRepositoryImpl = Depends(get_message_thread_repository),
) -> SuccessResponse:
    """List message threads for a store.

    GET /stores/{store_id}/inbox/threads
    Query: ?channel=&status=&unread_only=&search=&cursor=&limit=50
    """
    use_case = ListThreadsUseCase(message_thread_repository=thread_repo)
    result = await use_case.execute(
        store_id=store_id,
        channel=channel,
        status=status_filter,
        unread_only=unread_only,
        search=search,
        cursor=cursor,
        limit=limit,
    )
    return SuccessResponse(
        data=result,
        message=None,
    )


@router.get("/{thread_id}", response_model=dict, status_code=status.HTTP_200_OK)
async def get_thread(
    thread_id: UUID,
    db: AsyncSession = Depends(get_db),
    thread_repo: MessageThreadRepositoryImpl = Depends(get_message_thread_repository),
) -> SuccessResponse:
    """Get a specific thread by ID.

    GET /stores/{store_id}/inbox/threads/{thread_id}
    """
    use_case = GetThreadUseCase(message_thread_repository=thread_repo)
    thread = await use_case.execute(thread_id=thread_id)
    if not thread:
        return SuccessResponse(data=None, message="Thread not found")
    return SuccessResponse(data=thread, message=None)


@router.post("/{thread_id}/read", response_model=dict, status_code=status.HTTP_200_OK)
async def mark_read(
    thread_id: UUID,
    db: AsyncSession = Depends(get_db),
    thread_repo: MessageThreadRepositoryImpl = Depends(get_message_thread_repository),
) -> SuccessResponse:
    """Mark thread as read.

    POST /stores/{store_id}/inbox/threads/{thread_id}/read
    PATCH body: { "mark_read": true }
    """
    use_case = MarkThreadReadUseCase(message_thread_repository=thread_repo)
    await use_case.execute(thread_id=thread_id)
    return SuccessResponse(data=None, message="Thread marked as read")


@router.post(
    "/{thread_id}/resolve", response_model=dict, status_code=status.HTTP_200_OK
)
async def resolve_thread(
    thread_id: UUID,
    db: AsyncSession = Depends(get_db),
    thread_repo: MessageThreadRepositoryImpl = Depends(get_message_thread_repository),
) -> SuccessResponse:
    """Mark thread as resolved/closed.

    POST /stores/{store_id}/inbox/threads/{thread_id}/resolve
    PATCH body: { "status": "resolved" }
    """
    use_case = ResolveThreadUseCase(message_thread_repository=thread_repo)
    await use_case.execute(thread_id=thread_id)
    return SuccessResponse(data=None, message="Thread resolved")


__all__ = ["router"]
