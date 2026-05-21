"""WhatsApp template routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import (
    get_channel_connection_repository,
    get_whatsapp_template_repository,
)
from src.api.responses import SuccessResponse
from src.application.dto.omnichannel import (
    CreateTemplateDTO,
    WhatsAppTemplateDTO,
)
from src.application.use_cases.omnichannel import (
    CreateTemplateUseCase,
    ListTemplatesUseCase,
)
from src.infrastructure.repositories import (
    ChannelConnectionRepositoryImpl,
    WhatsAppTemplateRepositoryImpl,
)

router = APIRouter(tags=["Omnichannel"])


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_template(
    dto: CreateTemplateDTO,
    store_id: UUID,
    db: AsyncSession = Depends(get_db),
    connection_repo: ChannelConnectionRepositoryImpl = Depends(
        get_channel_connection_repository
    ),
    template_repo: WhatsAppTemplateRepositoryImpl = Depends(
        get_whatsapp_template_repository
    ),
) -> SuccessResponse:
    """Create a WhatsApp template.

    POST /stores/{store_id}/channels/whatsapp/templates
    Body: { "name", "category", "language", "components": [...] }
    """
    use_case = CreateTemplateUseCase(
        channel_connection_repository=connection_repo,
        whatsapp_template_repository=template_repo,
    )
    template = await use_case.execute(
        connection_id=dto.connection_id,
        name=dto.name,
        category=dto.category,
        language=dto.language,
        header=dto.header,
        body=dto.body,
        footer=dto.footer,
        buttons=dto.buttons,
    )
    return SuccessResponse(
        data={
            "id": template.id,
            "name": template.name,
            "status": template.status.value,
        },
        message="Template created successfully",
    )


@router.get("/", status_code=status.HTTP_200_OK)
async def list_templates(
    connection_id: UUID,
    status_filter: str | None = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_db),
    template_repo: WhatsAppTemplateRepositoryImpl = Depends(
        get_whatsapp_template_repository
    ),
) -> SuccessResponse:
    """List WhatsApp templates for a connection.

    GET /stores/{store_id}/channels/whatsapp/templates
    """
    use_case = ListTemplatesUseCase(
        whatsapp_template_repository=template_repo,
    )
    templates = await use_case.execute(
        channel_connection_id=connection_id,
        status=status_filter,
    )
    return SuccessResponse(
        data=[
            WhatsAppTemplateDTO(
                id=t.id,
                name=t.name,
                category=t.category,
                language=t.language,
                status=t.status,
                rejection_reason=t.rejection_reason,
            )
            for t in templates
        ],
        message=None,
    )


__all__ = ["router"]
