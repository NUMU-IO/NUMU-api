"""Admin (super-admin) email-template inspection routes.

URL: ``/api/v1/admin/email-templates``

Read-only for MVP — admins cannot edit registry defaults yet. Surface:

* ``GET    /events``                       — list every registered event
* ``GET    /events/{event_type}``          — registry default for one event
* ``POST   /events/{event_type}/preview``  — render the registry default
* ``POST   /events/{event_type}/send-test``— email rendered default to admin

Auth: every endpoint requires the platform super-admin guard
(:func:`require_admin`).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal
from uuid import UUID

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from jinja2 import ChainableUndefined, select_autoescape
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, EmailStr

from src.api.dependencies import (
    get_email_service,
    get_user_repository,
)
from src.api.dependencies.auth import require_admin
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.email_template import (
    DefaultTemplateResponse,
    EmailEventResponse,
    PreviewEmailResponse,
    SendTestEmailResponse,
)
from src.application.services.email_template_registry import (
    EMAIL_EVENT_REGISTRY,
    allowed_variables,
    get_event_spec,
    list_events,
)
from src.core.interfaces.services.email_service import EmailMessage, IEmailService
from src.infrastructure.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/email-templates")


# ─────────────────────────────────────────────────────────────────────────
# Rate limiter — process-local, 5 sends per 60s per (admin_user_id)
# ─────────────────────────────────────────────────────────────────────────


_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX = 5
_admin_test_counter: TTLCache[str, int] = TTLCache(
    maxsize=1024, ttl=_RATE_LIMIT_WINDOW_SECONDS
)


def _check_admin_rate_limit(admin_id: UUID) -> None:
    """Raise HTTP 429 once the admin has spent their per-window budget."""
    key = str(admin_id)
    current = _admin_test_counter.get(key, 0)
    if current >= _RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: max {_RATE_LIMIT_MAX} test sends per "
                f"{_RATE_LIMIT_WINDOW_SECONDS}s"
            ),
        )
    _admin_test_counter[key] = current + 1


# ─────────────────────────────────────────────────────────────────────────
# Local request bodies — admin variants don't need a saved template_id
# ─────────────────────────────────────────────────────────────────────────


class AdminPreviewRequest(BaseModel):
    """Body for the admin registry-default preview endpoint."""

    language: Literal["ar", "en"] = "ar"
    variables: dict[str, Any] | None = None


class AdminSendTestRequest(BaseModel):
    """Body for the admin registry-default send-test endpoint."""

    recipient: EmailStr
    language: Literal["ar", "en"] = "ar"
    variables: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


_GLOBAL_VARS = {"store_name"}


def _build_env() -> SandboxedEnvironment:
    """Build the same Jinja sandbox used by the runtime renderer."""
    return SandboxedEnvironment(
        autoescape=select_autoescape(["html", "htm"]),
        undefined=ChainableUndefined,
    )


def _spec_to_event_response(spec) -> EmailEventResponse:
    return EmailEventResponse(
        event_type=spec.event_type,
        label_en=spec.label_en,
        label_ar=spec.label_ar,
        variables=dict(spec.variables),
        sample_data=dict(spec.sample_data),
        default_subject_en=spec.default_subject.get("en", ""),
        default_subject_ar=spec.default_subject.get("ar", ""),
    )


def _ensure_event(event_type: str) -> None:
    if event_type not in EMAIL_EVENT_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown event type: {event_type!r}",
        )


def _render_default(
    *,
    event_type: str,
    language: str,
    variables: dict[str, Any] | None,
) -> tuple[str, str]:
    """Render the registry default for ``(event_type, language)``.

    Returns a ``(subject, html)`` tuple. Raises HTTP 400 on render error.
    """
    spec = get_event_spec(event_type)
    incoming = dict(variables) if variables else {}
    base: dict[str, Any] = dict(spec.sample_data)
    base.update(incoming)

    allowed = allowed_variables(event_type) | _GLOBAL_VARS
    filtered = {k: v for k, v in base.items() if k in allowed}

    subject_src = spec.default_subject.get(language, spec.default_subject.get("en", ""))
    body_src = spec.default_html.get(language, spec.default_html.get("en", ""))

    env = _build_env()
    try:
        rendered_subject = env.from_string(subject_src).render(**filtered)
        rendered_html = env.from_string(body_src).render(**filtered)
    except (TemplateError, Exception) as exc:  # noqa: BLE001
        logger.warning(
            "admin_email_template_render_failed",
            extra={
                "event_type": event_type,
                "language": language,
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template failed to render: {exc}",
        ) from exc

    return rendered_subject, rendered_html


# ─────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/events",
    response_model=SuccessResponse[list[EmailEventResponse]],
    summary="List registered email events (admin)",
    operation_id="admin_list_email_events",
)
async def admin_list_email_events(
    _admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Return the full registry catalog of customer-facing email events."""
    items = [_spec_to_event_response(spec) for spec in list_events()]
    return SuccessResponse(data=items, message="Email events catalog")


@router.get(
    "/events/{event_type}",
    response_model=SuccessResponse[DefaultTemplateResponse],
    summary="Get registry default template (admin)",
    operation_id="admin_get_email_event_default",
)
async def admin_get_email_event_default(
    event_type: Annotated[str, Path(max_length=50)],
    _admin_id: Annotated[UUID, Depends(require_admin)],
    language: Annotated[Literal["ar", "en"], Query(description="Language")] = "ar",
):
    """Return the registry-default subject + HTML body for an event."""
    _ensure_event(event_type)
    spec = get_event_spec(event_type)
    return SuccessResponse(
        data=DefaultTemplateResponse(
            event_type=event_type,
            language=language,
            subject=spec.default_subject.get(
                language, spec.default_subject.get("en", "")
            ),
            html_body=spec.default_html.get(language, spec.default_html.get("en", "")),
        ),
        message="Registry default template",
    )


@router.post(
    "/events/{event_type}/preview",
    response_model=SuccessResponse[PreviewEmailResponse],
    summary="Preview registry default template (admin)",
    operation_id="admin_preview_email_event",
)
async def admin_preview_email_event(
    event_type: Annotated[str, Path(max_length=50)],
    request: AdminPreviewRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Render the registry default for ``event_type`` against sample data."""
    _ensure_event(event_type)
    subject, html = _render_default(
        event_type=event_type,
        language=request.language,
        variables=request.variables,
    )
    return SuccessResponse(
        data=PreviewEmailResponse(subject=subject, html=html),
        message="Preview rendered",
    )


@router.post(
    "/events/{event_type}/send-test",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SuccessResponse[SendTestEmailResponse],
    summary="Send a test of the registry default template (admin)",
    operation_id="admin_send_test_email_event",
)
async def admin_send_test_email_event(
    event_type: Annotated[str, Path(max_length=50)],
    request: AdminSendTestRequest,
    admin_id: Annotated[UUID, Depends(require_admin)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    email_service: Annotated[IEmailService, Depends(get_email_service)],
):
    """Send a fully rendered test email of the registry default to the admin.

    Recipient is locked to the requesting admin's own email address to
    prevent abuse. Rate-limited to 5/min per admin.
    """
    _ensure_event(event_type)

    user = await user_repo.get_by_id(admin_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin user not found",
        )

    admin_email = str(user.email).strip().lower()
    if str(request.recipient).strip().lower() != admin_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Test emails can only be sent to your own admin account",
        )

    _check_admin_rate_limit(admin_id)

    subject, html = _render_default(
        event_type=event_type,
        language=request.language,
        variables=request.variables,
    )
    test_subject = f"[TEST] {subject}"

    sent = await email_service.send_email(
        EmailMessage(
            to=str(request.recipient),
            subject=test_subject,
            html_content=html,
        )
    )

    return SuccessResponse(
        data=SendTestEmailResponse(sent=bool(sent), message_id=None),
        message="Test email dispatched",
    )


__all__ = ["router"]
