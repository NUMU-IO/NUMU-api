"""Store-scoped email template management routes.

URL: ``/stores/{store_id}/email-templates``

Surface:
* ``GET    /events``                                     — list registered events
* ``GET    /events/{event_type}/default``                — registry default for (event, lang)
* ``GET    /``                                           — list merchant overrides
* ``GET    /{template_id}``                              — fetch one
* ``POST   /``                                           — create override
* ``PUT    /{template_id}``                              — update override
* ``DELETE /{template_id}``                              — delete override
* ``POST   /{template_id}/preview``                      — render this draft
* ``POST   /{template_id}/send-test``                    — email rendered test to merchant

All endpoints depend on ``verify_store_ownership`` so a merchant can
only touch templates for stores they own.
"""

from __future__ import annotations

import logging
from math import ceil
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from jinja2 import ChainableUndefined, select_autoescape
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment

from src.api.dependencies import (
    get_create_email_template_use_case,
    get_current_user_id,
    get_default_template_use_case,
    get_delete_email_template_use_case,
    get_email_template_repository,
    get_get_email_template_use_case,
    get_list_email_templates_use_case,
    get_send_test_email_use_case,
    get_update_email_template_use_case,
    get_user_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.api.v1.schemas.tenant.email_template import (
    CreateEmailTemplateRequest,
    DefaultTemplateResponse,
    EmailEventResponse,
    EmailTemplateResponse,
    PreviewDraftRequest,
    PreviewEmailRequest,
    PreviewEmailResponse,
    SendTestEmailRequest,
    SendTestEmailResponse,
    UpdateEmailTemplateRequest,
)
from src.application.dto.email_template import (
    CreateEmailTemplateDTO,
    EmailTemplateDTO,
    UpdateEmailTemplateDTO,
)
from src.application.services.email_template_registry import (
    EMAIL_EVENT_REGISTRY,
    allowed_variables,
    get_event_spec,
    list_events,
)
from src.application.services.email_template_sanitizer import strip_markdown_fences
from src.application.use_cases.email_templates import (
    CreateEmailTemplateUseCase,
    DeleteEmailTemplateUseCase,
    GetDefaultTemplateUseCase,
    GetEmailTemplateUseCase,
    ListEmailTemplatesUseCase,
    SendTestEmailUseCase,
    UpdateEmailTemplateUseCase,
)
from src.core.entities.store import Store
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.infrastructure.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/{store_id}/email-templates")


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


_GLOBAL_VARS = {"store_name"}


def _dto_to_response(dto: EmailTemplateDTO) -> EmailTemplateResponse:
    """Convert an :class:`EmailTemplateDTO` into the API response shape."""
    return EmailTemplateResponse(
        id=dto.id,
        store_id=dto.store_id,
        event_type=dto.event_type,
        language=dto.language,
        name=dto.name,
        subject=dto.subject,
        html_body=dto.html_body,
        is_enabled=dto.is_enabled,
        from_name=dto.from_name,
        reply_to=dto.reply_to,
        extra_data=dto.extra_data if dto.extra_data else None,
        created_at=dto.created_at,
        updated_at=dto.updated_at,
    )


def _spec_to_event_response(spec) -> EmailEventResponse:
    """Convert an :class:`EventSpec` to the API response shape."""
    return EmailEventResponse(
        event_type=spec.event_type,
        label_en=spec.label_en,
        label_ar=spec.label_ar,
        variables=dict(spec.variables),
        sample_data=dict(spec.sample_data),
        default_subject_en=spec.default_subject.get("en", ""),
        default_subject_ar=spec.default_subject.get("ar", ""),
    )


def _build_preview_env() -> SandboxedEnvironment:
    """Build the same Jinja sandbox the runtime renderer uses."""
    return SandboxedEnvironment(
        autoescape=select_autoescape(["html", "htm"]),
        undefined=ChainableUndefined,
    )


# ─────────────────────────────────────────────────────────────────────────
# Events catalog (registry-only; no DB access)
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/events",
    response_model=SuccessResponse[list[EmailEventResponse]],
    summary="List registered email events",
    operation_id="list_email_events",
)
async def list_email_events_endpoint(
    store_id: Annotated[UUID, Path(description="Store ID")],
    _: Annotated[Store, Depends(verify_store_ownership)],
):
    """Return the registry of customer-facing email events.

    Pure registry lookup — no DB query. The registry is the single
    source of truth for event types, declared variables, sample data,
    and the default subject in each language.
    """
    items = [_spec_to_event_response(spec) for spec in list_events()]
    return SuccessResponse(data=items, message="Email events catalog")


@router.get(
    "/events/{event_type}/default",
    response_model=SuccessResponse[DefaultTemplateResponse],
    summary="Get registry default template for an event/language",
    operation_id="get_default_email_template",
)
async def get_default_email_template_endpoint(
    store_id: Annotated[UUID, Path(description="Store ID")],
    event_type: Annotated[str, Path(max_length=50, description="Event identifier")],
    _: Annotated[Store, Depends(verify_store_ownership)],
    use_case: Annotated[
        GetDefaultTemplateUseCase, Depends(get_default_template_use_case)
    ],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    language: Annotated[Literal["ar", "en"], Query(description="Language")] = "ar",
):
    """Return the registry-default subject + HTML body for the given event."""
    result = await use_case.execute(
        store_id=store_id,
        event_type=event_type,
        language=language,
        user_id=user_id,
    )
    return SuccessResponse(
        data=DefaultTemplateResponse(
            event_type=result.event_type,
            language=result.language,
            subject=result.subject,
            html_body=result.html_body,
        ),
        message="Registry default template",
    )


# ─────────────────────────────────────────────────────────────────────────
# Merchant override CRUD
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[EmailTemplateResponse]],
    summary="List merchant email templates",
    operation_id="list_email_templates",
)
async def list_email_templates_endpoint(
    store_id: Annotated[UUID, Path(description="Store ID")],
    _: Annotated[Store, Depends(verify_store_ownership)],
    use_case: Annotated[
        ListEmailTemplatesUseCase, Depends(get_list_email_templates_use_case)
    ],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    event_type: Annotated[str | None, Query(max_length=50)] = None,
    language: Annotated[Literal["ar", "en"] | None, Query()] = None,
    is_enabled: Annotated[bool | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """List merchant-customized templates for a store, with optional filters."""
    skip = (page - 1) * limit
    items, total = await use_case.execute(
        store_id=store_id,
        user_id=user_id,
        event_type=event_type,
        language=language,
        is_enabled=is_enabled,
        skip=skip,
        limit=limit,
    )
    total_pages = ceil(total / limit) if total > 0 else 0
    return SuccessResponse(
        data=PaginatedListResponse(
            items=[_dto_to_response(t) for t in items],
            total=total,
            page=page,
            page_size=limit,
            total_pages=total_pages,
        ),
        message="Email templates",
    )


@router.get(
    "/{template_id}",
    response_model=SuccessResponse[EmailTemplateResponse],
    summary="Get email template by ID",
    operation_id="get_email_template",
)
async def get_email_template_endpoint(
    store_id: Annotated[UUID, Path(description="Store ID")],
    template_id: Annotated[UUID, Path(description="Template ID")],
    _: Annotated[Store, Depends(verify_store_ownership)],
    use_case: Annotated[
        GetEmailTemplateUseCase, Depends(get_get_email_template_use_case)
    ],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Fetch a single email template by id."""
    result = await use_case.execute(
        store_id=store_id, template_id=template_id, user_id=user_id
    )
    return SuccessResponse(
        data=_dto_to_response(result), message="Email template retrieved"
    )


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[EmailTemplateResponse],
    summary="Create email template override",
    operation_id="create_email_template",
)
async def create_email_template_endpoint(
    request: CreateEmailTemplateRequest,
    store_id: Annotated[UUID, Path(description="Store ID")],
    _: Annotated[Store, Depends(verify_store_ownership)],
    use_case: Annotated[
        CreateEmailTemplateUseCase, Depends(get_create_email_template_use_case)
    ],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Create a new merchant-customized email template."""
    dto = CreateEmailTemplateDTO(
        event_type=request.event_type,
        language=request.language,
        name=request.name,
        subject=request.subject,
        html_body=request.html_body,
        is_enabled=request.is_enabled,
        from_name=request.from_name,
        reply_to=str(request.reply_to) if request.reply_to else None,
        extra_data=request.extra_data or {},
    )
    result = await use_case.execute(store_id=store_id, dto=dto, user_id=user_id)
    return SuccessResponse(
        data=_dto_to_response(result), message="Email template created"
    )


@router.put(
    "/{template_id}",
    response_model=SuccessResponse[EmailTemplateResponse],
    summary="Update email template",
    operation_id="update_email_template",
)
async def update_email_template_endpoint(
    request: UpdateEmailTemplateRequest,
    store_id: Annotated[UUID, Path(description="Store ID")],
    template_id: Annotated[UUID, Path(description="Template ID")],
    _: Annotated[Store, Depends(verify_store_ownership)],
    use_case: Annotated[
        UpdateEmailTemplateUseCase, Depends(get_update_email_template_use_case)
    ],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Update an existing email template — only provided fields are touched."""
    dto = UpdateEmailTemplateDTO(
        name=request.name,
        subject=request.subject,
        html_body=request.html_body,
        is_enabled=request.is_enabled,
        from_name=request.from_name,
        reply_to=str(request.reply_to) if request.reply_to else None,
        extra_data=request.extra_data,
    )
    result = await use_case.execute(
        store_id=store_id, template_id=template_id, dto=dto, user_id=user_id
    )
    return SuccessResponse(
        data=_dto_to_response(result), message="Email template updated"
    )


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete email template",
    operation_id="delete_email_template",
)
async def delete_email_template_endpoint(
    store_id: Annotated[UUID, Path(description="Store ID")],
    template_id: Annotated[UUID, Path(description="Template ID")],
    _: Annotated[Store, Depends(verify_store_ownership)],
    use_case: Annotated[
        DeleteEmailTemplateUseCase, Depends(get_delete_email_template_use_case)
    ],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Delete a merchant-customized email template."""
    await use_case.execute(store_id=store_id, template_id=template_id, user_id=user_id)
    return None


# ─────────────────────────────────────────────────────────────────────────
# Preview & send-test
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/preview-draft",
    response_model=SuccessResponse[PreviewEmailResponse],
    summary="Preview an unsaved email-template draft",
    operation_id="preview_email_template_draft",
)
async def preview_email_template_draft_endpoint(
    request: PreviewDraftRequest,
    store_id: Annotated[UUID, Path(description="Store ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    """Render the in-flight editor buffer without persisting anything.

    The merchant editor calls this on every debounced keystroke so the
    preview iframe always reflects their current draft (whether they're
    creating a new template or editing an existing one with unsaved
    changes). Mirrors the rendering logic of the saved-template preview
    endpoint — same Jinja sandbox, same variable whitelist — minus the
    DB lookup.
    """
    spec = (
        get_event_spec(request.event_type)
        if request.event_type in EMAIL_EVENT_REGISTRY
        else None
    )
    incoming = dict(request.variables) if request.variables else {}
    base_vars: dict[str, object] = dict(spec.sample_data) if spec else {}
    base_vars.update(incoming)
    base_vars.setdefault("store_name", store.name)

    if spec is not None:
        allowed = allowed_variables(request.event_type) | _GLOBAL_VARS
        filtered_vars = {k: v for k, v in base_vars.items() if k in allowed}
    else:
        filtered_vars = {k: v for k, v in base_vars.items() if k in _GLOBAL_VARS}

    env = _build_preview_env()
    # Strip wrapping markdown code fences merchants sometimes paste in.
    body_src = strip_markdown_fences(request.html_body)
    try:
        rendered_subject = env.from_string(request.subject).render(**filtered_vars)
        rendered_html = env.from_string(body_src).render(**filtered_vars)
    except (TemplateError, Exception) as exc:  # noqa: BLE001
        logger.warning(
            "email_template_preview_draft_failed",
            extra={
                "store_id": str(store_id),
                "event_type": request.event_type,
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template failed to render: {exc}",
        ) from exc

    # Render raw — merchant owns 100% of the visual. The platform no
    # longer forces a NUMU header / footer on top of the merchant's HTML.

    return SuccessResponse(
        data=PreviewEmailResponse(subject=rendered_subject, html=rendered_html),
        message="Draft preview rendered",
    )


@router.post(
    "/{template_id}/preview",
    response_model=SuccessResponse[PreviewEmailResponse],
    summary="Preview a saved email template",
    operation_id="preview_email_template",
)
async def preview_email_template_endpoint(
    request: PreviewEmailRequest,
    store_id: Annotated[UUID, Path(description="Store ID")],
    template_id: Annotated[UUID, Path(description="Template ID")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    template_repo: Annotated[
        IEmailTemplateRepository, Depends(get_email_template_repository)
    ],
):
    """Render the saved template *as is*, even if it's currently disabled.

    Unlike :class:`EmailTemplateRenderer.render` (which uses
    ``get_for_send`` and only ever sees enabled rows), preview must show
    the merchant their draft. We render directly in the same sandbox the
    runtime renderer uses, with the same variable whitelist applied.
    """
    template = await template_repo.get_by_id(template_id)
    if not template or template.store_id != store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email template not found",
        )

    # Build render variables: caller-supplied OR registry sample as fallback.
    spec = (
        get_event_spec(template.event_type)
        if template.event_type in EMAIL_EVENT_REGISTRY
        else None
    )
    incoming = dict(request.variables) if request.variables else {}
    base_vars: dict[str, object] = dict(spec.sample_data) if spec else {}
    base_vars.update(incoming)
    # Overlay the merchant's actual store name so previews aren't tagged
    # with the registry's "Cairo Threads" sample.
    base_vars.setdefault("store_name", store.name)

    # Filter to the event's allow-list (plus the implicit globals) so a
    # malicious or sloppy preview can't smuggle extra context to Jinja.
    if spec is not None:
        allowed = allowed_variables(template.event_type) | _GLOBAL_VARS
        filtered_vars = {k: v for k, v in base_vars.items() if k in allowed}
    else:
        filtered_vars = {k: v for k, v in base_vars.items() if k in _GLOBAL_VARS}

    env = _build_preview_env()
    body_src = strip_markdown_fences(template.html_body)
    try:
        rendered_subject = env.from_string(template.subject).render(**filtered_vars)
        rendered_html = env.from_string(body_src).render(**filtered_vars)
    except (TemplateError, Exception) as exc:  # noqa: BLE001
        logger.warning(
            "email_template_preview_failed",
            extra={
                "template_id": str(template_id),
                "store_id": str(store_id),
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template failed to render: {exc}",
        ) from exc

    # Render raw — merchant owns 100% of the visual.

    return SuccessResponse(
        data=PreviewEmailResponse(subject=rendered_subject, html=rendered_html),
        message="Preview rendered",
    )


@router.post(
    "/{template_id}/send-test",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessResponse[SendTestEmailResponse],
    summary="Send a test email rendered from this template",
    operation_id="send_test_email_template",
)
async def send_test_email_endpoint(
    request: SendTestEmailRequest,
    store_id: Annotated[UUID, Path(description="Store ID")],
    template_id: Annotated[UUID, Path(description="Template ID")],
    _: Annotated[Store, Depends(verify_store_ownership)],
    use_case: Annotated[SendTestEmailUseCase, Depends(get_send_test_email_use_case)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Send a fully rendered test email to the requesting merchant.

    The use case enforces that ``recipient`` matches the requesting
    user's own email address and applies a 5/min rate limit per
    ``(user_id, template_id)``.
    """
    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Send-test failures are almost always Resend configuration issues
    # (unverified sending domain, missing API key, etc.). Surfacing the
    # actual error message — instead of the generic
    # ``EXTERNAL_SERVICE_ERROR`` the global handler produces — saves the
    # merchant a long debugging loop.
    from src.core.exceptions import ExternalServiceError

    try:
        result = await use_case.execute(
            store_id=store_id,
            template_id=template_id,
            recipient=str(request.recipient),
            variables=request.variables,
            user_id=user_id,
            user_email=str(user.email),
        )
    except ExternalServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Email provider rejected the send: {exc}",
        ) from exc

    return SuccessResponse(
        data=SendTestEmailResponse(
            sent=bool(result.get("sent", False)),
            message_id=result.get("message_id"),
        ),
        message="Test email dispatched",
    )


__all__ = ["router"]
