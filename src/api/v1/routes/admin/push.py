"""Web Push for the admin backoffice PWA.

URL: /api/v1/admin/push — requires SUPER_ADMIN.

Separate from `/auth/me/push-token` rather than reusing it, for one reason
that is not stylistic: that endpoint resolves the caller's owner tenant and
rejects anyone who has none. A platform operator has none by definition, so it
can never serve this client. Everything below it is shared — the same
`device_registrations` table, the same VAPID keys, the same fan-out — and a
staff row is marked by `tenant_id` being NULL.

A subscription registered here therefore receives PLATFORM notifications only:
a top-up proof waiting on review, a WhatsApp access request, a theme
submission. It is never reached by a merchant fan-out, which reads a tenant.
"""

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.config.settings import settings
from src.infrastructure.repositories.device_registration_repository import (
    DeviceRegistrationRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class PushKeyResponse(BaseModel):
    public_key: str | None
    enabled: bool


class SubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)
    p256dh: str | None = Field(default=None, max_length=256)
    auth: str | None = Field(default=None, max_length=128)
    locale: str | None = Field(default=None, max_length=8)
    platform: Literal["web"] = "web"


@router.get(
    "/key",
    response_model=SuccessResponse[PushKeyResponse],
    summary="VAPID public key for the admin PWA",
    operation_id="admin_get_push_key",
)
async def get_push_key(
    _admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Return the VAPID public key, or `enabled: false` when push is unconfigured.

    Not a 404 when unconfigured: the client uses `enabled` to hide the toggle
    entirely, and an error would read as a bug rather than as a deployment
    that has no VAPID keys.
    """
    return SuccessResponse(
        data=PushKeyResponse(
            public_key=settings.VAPID_PUBLIC_KEY,
            enabled=settings.web_push_enabled,
        )
    )


@router.post(
    "/subscribe",
    response_model=SuccessResponse[dict],
    summary="Register this browser for platform notifications",
    operation_id="admin_register_push",
)
async def subscribe(
    payload: SubscribeRequest,
    http_request: Request,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Upsert the calling browser's subscription as a platform device.

    Idempotent on `endpoint`, like the merchant path: browsers re-issue the
    same endpoint on every `subscribe()` call, so anything else would grow a
    row per page load.

    SECURITY: the user is taken from the admin session, never from the body.
    """
    repo = DeviceRegistrationRepository(db)
    await repo.upsert(
        # NULL: this device belongs to the platform, not to a store.
        tenant_id=None,
        user_id=admin_id,
        endpoint=payload.endpoint,
        provider="webpush",
        platform=payload.platform,
        p256dh=payload.p256dh,
        auth=payload.auth,
        locale=payload.locale,
        user_agent=http_request.headers.get("user-agent"),
    )
    await db.commit()
    return SuccessResponse(data={"registered": True})


@router.delete(
    "/subscribe",
    response_model=SuccessResponse[dict],
    summary="Revoke this browser's platform notifications",
    operation_id="admin_revoke_push",
)
async def unsubscribe(
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    endpoint: str | None = None,
):
    """Revoke one endpoint, or every device this operator registered.

    Called on logout as well as from the toggle. A signed-out laptop must stop
    receiving the platform's queue alerts — a shared machine in the office is
    the normal case, not the exception.
    """
    repo = DeviceRegistrationRepository(db)
    revoked = await repo.revoke(user_id=admin_id, endpoint=endpoint)
    await db.commit()
    return SuccessResponse(data={"revoked": revoked})


@router.post(
    "/test",
    response_model=SuccessResponse[dict],
    summary="Send a test notification to every staff device",
    operation_id="admin_test_push",
)
async def send_test(
    _admin_id: Annotated[UUID, Depends(require_admin)],
):
    """Prove the whole chain — VAPID keys, Celery, service worker — in one tap.

    Without this the first real notification is also the first test, and when
    nothing arrives there is no way to tell whether the subscription, the
    broker or the worker is at fault.
    """
    from src.infrastructure.messaging.tasks.push_tasks import notify_admins

    if not settings.web_push_enabled:
        return SuccessResponse(data={"queued": False, "reason": "push_not_configured"})

    notify_admins(
        title="NUMU Admin",
        body="Notifications are working.",
        url="/",
        tag="admin:test",
    )
    return SuccessResponse(data={"queued": True})
