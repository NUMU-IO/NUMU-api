"""Channel connection routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_channel_connection_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.application.dto.omnichannel import (
    ChannelConnectionDTO,
    ConnectMetaCallbackDTO,
    ConnectMetaDTO,
)
from src.application.use_cases.omnichannel import (
    ConnectMetaUseCase,
    DisconnectChannelUseCase,
)
from src.core.entities.channel_connection import ChannelConnection
from src.infrastructure.repositories import (
    ChannelConnectionRepositoryImpl,
    StoreRepository,
)

router = APIRouter(tags=["Omnichannel"])


async def _build_connection_response(conn: ChannelConnection) -> ChannelConnectionDTO:
    """Build ChannelConnectionDTO from entity."""
    return ChannelConnectionDTO(
        id=conn.id,
        channel=conn.channel.value,
        status=conn.status.value,
        external_account_id=conn.external_account_id,
        external_account_name=conn.external_account_name,
        external_phone_number_id=conn.external_phone_number_id,
        is_active=conn.is_active,
        token_expires_at=conn.token_expires_at.isoformat()
        if conn.token_expires_at
        else None,
    )


@router.post("/connect", status_code=status.HTTP_200_OK)
async def connect_meta(
    dto: ConnectMetaDTO,
    store_id: UUID,
    db: AsyncSession = Depends(get_db),
    channel_connection_repo: ChannelConnectionRepositoryImpl = Depends(
        get_channel_connection_repository
    ),
    store_repo: StoreRepository = Depends(get_store_repository),
) -> SuccessResponse:
    """Start Meta OAuth flow - returns authorization URL."""
    use_case = ConnectMetaUseCase(
        channel_connection_repository=channel_connection_repo,
        store_repository=store_repo,
    )
    auth_url, state = await use_case.start_oauth(
        store_id=dto.store_id,
        redirect_uri=dto.redirect_uri,
    )
    return SuccessResponse(
        data={"authorization_url": auth_url, "state": state},
        message="Navigate to authorization_url to complete OAuth",
    )


@router.post("/callback", status_code=status.HTTP_200_OK)
async def meta_callback(
    dto: ConnectMetaCallbackDTO,
    store_id: UUID,
    db: AsyncSession = Depends(get_db),
    channel_connection_repo: ChannelConnectionRepositoryImpl = Depends(
        get_channel_connection_repository
    ),
    store_repo: StoreRepository = Depends(get_store_repository),
) -> SuccessResponse:
    """Handle OAuth callback - exchange code for tokens."""
    use_case = ConnectMetaUseCase(
        channel_connection_repository=channel_connection_repo,
        store_repository=store_repo,
    )
    connections = await use_case.handle_callback(dto=dto, store_id=store_id)
    return SuccessResponse(
        data=[_build_connection_response(conn) for conn in connections],
        message="Channels connected successfully",
    )


@router.delete("/{connection_id}", status_code=status.HTTP_200_OK)
async def disconnect_channel(
    connection_id: UUID,
    store_id: UUID,
    db: AsyncSession = Depends(get_db),
    channel_connection_repo: ChannelConnectionRepositoryImpl = Depends(
        get_channel_connection_repository
    ),
) -> SuccessResponse:
    """Disconnect a channel connection."""
    use_case = DisconnectChannelUseCase(
        channel_connection_repository=channel_connection_repo,
    )
    await use_case.execute(connection_id=connection_id, store_id=store_id)
    return SuccessResponse(data=None, message="Channel disconnected successfully")


@router.get("/", status_code=status.HTTP_200_OK)
async def list_connections(
    store_id: UUID,
    channel: str | None = Query(None, description="Filter by channel type"),
    db: AsyncSession = Depends(get_db),
    channel_connection_repo: ChannelConnectionRepositoryImpl = Depends(
        get_channel_connection_repository
    ),
) -> SuccessResponse:
    """List all channel connections for a store."""
    connections = await channel_connection_repo.get_by_store(store_id)
    return SuccessResponse(
        data=[_build_connection_response(conn) for conn in connections],
        message=None,
    )


__all__ = ["router"]
