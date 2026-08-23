"""Message thread routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_customer_repository,
    get_message_thread_repository,
)
from src.api.dependencies.services import get_storage_service
from src.api.responses import SuccessResponse
from src.application.dto.omnichannel import LinkCustomerDTO
from src.application.services.avatar_adoption import adopt_avatar
from src.application.use_cases.omnichannel import (
    GetThreadUseCase,
    ListThreadsUseCase,
    MarkThreadReadUseCase,
    ResolveThreadUseCase,
)
from src.core.exceptions import EntityNotFoundError
from src.core.logging import get_logger
from src.infrastructure.repositories import MessageThreadRepositoryImpl
from src.infrastructure.repositories.customer_repository import CustomerRepository

logger = get_logger(__name__)

router = APIRouter(tags=["Omnichannel"])


@router.get("/", response_model=SuccessResponse, status_code=status.HTTP_200_OK)
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


@router.get(
    "/{thread_id}", response_model=SuccessResponse, status_code=status.HTTP_200_OK
)
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


@router.post(
    "/{thread_id}/read", response_model=SuccessResponse, status_code=status.HTTP_200_OK
)
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
    "/{thread_id}/resolve",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
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


@router.post(
    "/{thread_id}/customer",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
)
async def link_customer(
    thread_id: UUID,
    store_id: UUID,
    dto: LinkCustomerDTO,
    db: AsyncSession = Depends(get_db),
    thread_repo: MessageThreadRepositoryImpl = Depends(get_message_thread_repository),
    customer_repo: CustomerRepository = Depends(get_customer_repository),
    storage=Depends(get_storage_service),
) -> SuccessResponse:
    """Link this conversation to a customer record.

    Deliberate and reversible: Meta gives us no phone number, and guessing
    from a display name would merge two different people into one customer
    history. An agent confirms who they're talking to, then links.
    """
    thread = await thread_repo.get_by_id(thread_id)
    if not thread or thread.store_id != store_id:
        raise EntityNotFoundError("Thread not found")

    customer = await customer_repo.get_by_id(dto.customer_id)
    if not customer or customer.store_id != store_id:
        raise EntityNotFoundError("Customer not found")

    thread.customer_id = dto.customer_id
    # A linked customer is the better identity: show their real phone in
    # the inbox instead of nothing. customer.phone is a PhoneNumber value
    # object, so take its canonical E.164 string.
    if customer.phone and not thread.participant_phone_e164:
        thread.participant_phone_e164 = getattr(customer.phone, "value", None) or str(
            customer.phone
        )
    await thread_repo.update(thread)

    # First link wins the photo: a customer with no picture adopts this
    # conversation's avatar automatically (re-hosted; best-effort — the
    # link itself never fails because of it). The profile page can still
    # override with another linked conversation's photo.
    meta = customer.metadata if isinstance(customer.metadata, dict) else {}
    if thread.participant_avatar_url and not meta.get("avatar_url"):
        try:
            await adopt_avatar(
                customer=customer,
                source_url=thread.participant_avatar_url,
                thread_id=thread.id,
                storage=storage,
                customer_repo=customer_repo,
            )
        except Exception:  # noqa: BLE001 — cosmetic, never block the link
            logger.warning(
                "link_customer_avatar_adopt_failed", thread_id=str(thread.id)
            )

    return SuccessResponse(data=None, message="Conversation linked to customer")


@router.delete(
    "/{thread_id}/customer",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
)
async def unlink_customer(
    thread_id: UUID,
    store_id: UUID,
    db: AsyncSession = Depends(get_db),
    thread_repo: MessageThreadRepositoryImpl = Depends(get_message_thread_repository),
) -> SuccessResponse:
    """Remove the customer link from this conversation."""
    thread = await thread_repo.get_by_id(thread_id)
    if not thread or thread.store_id != store_id:
        raise EntityNotFoundError("Thread not found")

    thread.customer_id = None
    await thread_repo.update(thread)

    return SuccessResponse(data=None, message="Customer link removed")


__all__ = ["router"]
