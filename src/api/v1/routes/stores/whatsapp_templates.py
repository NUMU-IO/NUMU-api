"""WhatsApp template management routes."""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_store
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.schemas.stores.whatsapp import (
    TemplateCreate,
    TemplateListResponse,
    TemplateResponse,
)
from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)
from src.infrastructure.repositories.whatsapp_template_repository import (
    WhatsAppTemplateRepository,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/{store_id}/whatsapp/templates")


def _model_to_response(m: WhatsAppTemplateModel) -> TemplateResponse:
    return TemplateResponse(
        id=m.id,
        store_id=m.store_id,
        meta_template_id=m.meta_template_id,
        name=m.name,
        language=m.language,
        category=m.category,
        status=m.status,
        header_type=m.header_type,
        header_content=m.header_content,
        body_text=m.body_text,
        footer_text=m.footer_text,
        buttons=m.buttons or [],
        is_system=m.is_system,
        submitted_at=m.submitted_at,
        approved_at=m.approved_at,
        rejection_reason=m.rejection_reason,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get(
    "",
    response_model=SuccessResponse[TemplateListResponse],
    summary="List WhatsApp templates",
    operation_id="list_whatsapp_templates",
)
async def list_templates(
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
    category: str | None = Query(None),
    template_status: str | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    repo = WhatsAppTemplateRepository(db)
    templates, total = await repo.list_by_store(
        store.id, category=category, status=template_status, skip=skip, limit=limit
    )
    return SuccessResponse(
        data=TemplateListResponse(
            templates=[_model_to_response(t) for t in templates],
            total=total,
        ),
        message="Templates retrieved",
    )


@router.post(
    "",
    response_model=SuccessResponse[TemplateResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create WhatsApp template",
    operation_id="create_whatsapp_template",
)
async def create_template(
    request: TemplateCreate,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    """Create a template locally and submit to Meta for approval."""
    repo = WhatsAppTemplateRepository(db)

    # Check duplicate
    existing = await repo.get_by_name(store.id, request.name, request.language)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Template '{request.name}' ({request.language}) already exists",
        )

    model = WhatsAppTemplateModel(
        store_id=store.id,
        tenant_id=store.tenant_id,
        name=request.name,
        language=request.language,
        category=request.category,
        status="PENDING",
        header_type=request.header_type,
        header_content=request.header_content,
        body_text=request.body_text,
        footer_text=request.footer_text,
        buttons=[b.model_dump() for b in request.buttons] if request.buttons else None,
        submitted_at=datetime.now(UTC),
    )
    created = await repo.create(model)

    # Submit to Meta if store has own WABA
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.repositories.credential_repository import (
        CredentialRepository,
    )

    cred_repo = CredentialRepository(db)
    creds = await cred_repo.get_decrypted_credentials(
        tenant_id=store.tenant_id,
        service_type=ServiceType.WHATSAPP,
        service_name=ServiceName.WHATSAPP_BUSINESS,
    )
    if creds:
        from src.infrastructure.external_services.whatsapp.template_service import (
            WhatsAppTemplateService,
        )

        svc = WhatsAppTemplateService(
            access_token=creds["access_token"],
            waba_id=creds["waba_id"],
        )
        meta_result = await svc.create_template(
            name=request.name,
            language=request.language,
            category=request.category,
            body_text=request.body_text,
            header_type=request.header_type,
            header_content=request.header_content,
            footer_text=request.footer_text,
            buttons=[b.model_dump() for b in request.buttons]
            if request.buttons
            else None,
        )
        if meta_result:
            created.meta_template_id = meta_result.get("id")
            await db.flush()
            await db.refresh(created)

    return SuccessResponse(
        data=_model_to_response(created),
        message="Template created and submitted for approval",
    )


@router.delete(
    "/{template_id}",
    response_model=SuccessResponse[dict],
    summary="Delete WhatsApp template",
    operation_id="delete_whatsapp_template",
)
async def delete_template(
    template_id: UUID,
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    repo = WhatsAppTemplateRepository(db)
    template = await repo.get_by_id(template_id)
    if not template or template.store_id != store.id:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system templates")

    # Delete from Meta if store has own WABA
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.repositories.credential_repository import (
        CredentialRepository,
    )

    cred_repo = CredentialRepository(db)
    creds = await cred_repo.get_decrypted_credentials(
        tenant_id=store.tenant_id,
        service_type=ServiceType.WHATSAPP,
        service_name=ServiceName.WHATSAPP_BUSINESS,
    )
    if creds:
        from src.infrastructure.external_services.whatsapp.template_service import (
            WhatsAppTemplateService,
        )

        svc = WhatsAppTemplateService(
            access_token=creds["access_token"],
            waba_id=creds["waba_id"],
        )
        await svc.delete_template(template.name)

    await repo.delete(template_id)
    return SuccessResponse(data={}, message="Template deleted")


@router.post(
    "/sync",
    response_model=SuccessResponse[TemplateListResponse],
    summary="Sync template statuses from Meta",
    operation_id="sync_whatsapp_templates",
)
async def sync_templates(
    store: Annotated[Store, Depends(get_current_store)],
    db: AsyncSession = Depends(get_db),
):
    """Fetch latest template statuses from Meta and update local DB."""
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.repositories.credential_repository import (
        CredentialRepository,
    )

    cred_repo = CredentialRepository(db)
    creds = await cred_repo.get_decrypted_credentials(
        tenant_id=store.tenant_id,
        service_type=ServiceType.WHATSAPP,
        service_name=ServiceName.WHATSAPP_BUSINESS,
    )
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="Store does not have its own WhatsApp Business account",
        )

    from src.infrastructure.external_services.whatsapp.template_service import (
        WhatsAppTemplateService,
    )

    svc = WhatsAppTemplateService(
        access_token=creds["access_token"],
        waba_id=creds["waba_id"],
    )

    repo = WhatsAppTemplateRepository(db)
    templates, total = await repo.list_by_store(store.id, limit=500)

    local_list = [{"name": t.name, "language": t.language} for t in templates]
    updates = await svc.sync_statuses(local_list)

    # Apply updates
    update_map = {(u["name"], u["language"]): u for u in updates}
    for t in templates:
        upd = update_map.get((t.name, t.language))
        if upd:
            t.status = upd["status"]
            if upd.get("meta_template_id"):
                t.meta_template_id = upd["meta_template_id"]
            if upd.get("rejection_reason"):
                t.rejection_reason = upd["rejection_reason"]
            if upd["status"] == "APPROVED" and not t.approved_at:
                t.approved_at = datetime.now(UTC)
    await db.flush()

    # Re-fetch
    templates, total = await repo.list_by_store(store.id, limit=500)
    return SuccessResponse(
        data=TemplateListResponse(
            templates=[_model_to_response(t) for t in templates],
            total=total,
        ),
        message="Templates synced from Meta",
    )
