"""Per-store Marketing-Meta connect/disconnect.

Persists / clears the Meta Marketing API credential row
(``service_name=META_MARKETING``) and updates
``store.settings.tracking.meta.ad_account_id``.

The /connect endpoint is the second leg of the OAuth flow at
``/api/v1/oauth/meta-marketing/{start,callback}``: the merchant-hub
takes the token + ad-accounts list returned by /callback, lets the
merchant pick an account, then POSTs here to commit.

Why separate from the existing META_CAPI persistence in
``stores/settings.py``: those endpoints store the Pixel/CAPI token
that gets used for event-tracking only. Promote-on-Meta and Custom
Audiences both need ads_management scope, which CAPI tokens never
have — see ``ServiceName.META_MARKETING`` for context.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.api.dependencies import get_current_store
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.config.logging_config import get_logger
from src.core.entities.store import Store
from src.infrastructure.database.models.tenant.configuration import (
    ServiceCredential,
    ServiceName,
    ServiceType,
)
from src.infrastructure.external_services.meta.oauth_client import MetaOAuthClient
from src.infrastructure.external_services.secrets.secrets_manager import (
    get_secrets_manager,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/{store_id}/settings/marketing/meta")


class ConnectMetaMarketingRequest(BaseModel):
    """Body for the /connect endpoint.

    ``access_token`` is the long-lived token from the OAuth callback;
    ``ad_account_id`` is the bare numeric id (no ``act_`` prefix) the
    merchant picked from the picker.
    """

    access_token: str = Field(min_length=20, description="Long-lived Meta token")
    ad_account_id: str = Field(min_length=1, description="Ad account id (no 'act_')")


class MarketingMetaStatusResponse(BaseModel):
    connected: bool
    ad_account_id: str | None
    granted_scopes: list[str] | None
    last_validated_at: datetime | None


def _get_marketing_cred_q(tenant_id):
    return (
        _select(ServiceCredential)
        .where(ServiceCredential.tenant_id == tenant_id)
        .where(ServiceCredential.service_type == ServiceType.TRACKING)
        .where(ServiceCredential.service_name == ServiceName.META_MARKETING)
    )


@router.get(
    "",
    response_model=SuccessResponse[MarketingMetaStatusResponse],
    summary="Get Meta Marketing connection status",
    operation_id="get_meta_marketing_status",
)
async def get_status(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    cred = (
        await db.execute(_get_marketing_cred_q(store.tenant_id))
    ).scalar_one_or_none()
    cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
    return SuccessResponse(
        data=MarketingMetaStatusResponse(
            connected=cred is not None and cred.is_active,
            ad_account_id=cfg.get("ad_account_id"),
            granted_scopes=(cred.extra_metadata or {}).get("granted_scopes")
            if cred
            else None,
            last_validated_at=cred.last_validated_at if cred else None,
        ),
        message="Marketing-Meta status",
    )


@router.post(
    "/connect",
    response_model=SuccessResponse[MarketingMetaStatusResponse],
    summary="Persist Meta Marketing token + ad account picked from OAuth",
    operation_id="connect_meta_marketing",
)
async def connect(
    body: ConnectMetaMarketingRequest,
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Finalize the marketing OAuth flow.

    Re-validates the token + ad-account pair against Meta before
    persisting. We don't trust the access_token blindly because the
    merchant-hub passes it client-side from /callback's response —
    a hostile client could submit anything.
    """
    client = MetaOAuthClient()
    debug = await client.debug_token(access_token=body.access_token)
    if not debug or not debug.get("is_valid"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Meta rejected the access token (debug_token said invalid).",
        )
    granted = list(debug.get("scopes") or [])
    if "ads_management" not in granted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Token does not have ads_management — re-run the OAuth "
                "flow and accept all requested permissions."
            ),
        )

    accounts = await client.list_ad_accounts(access_token=body.access_token)
    if not any(
        str(a.get("account_id") or "") == body.ad_account_id
        or str(a.get("id") or "") == f"act_{body.ad_account_id}"
        for a in accounts
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Ad account {body.ad_account_id} is not reachable by "
                "this token. Pick an account from the picker."
            ),
        )

    sm = get_secrets_manager()
    key_id = await sm.get_current_key_id()
    encrypted = await sm.encrypt({"access_token": body.access_token}, key_id)

    cred = (
        await db.execute(_get_marketing_cred_q(store.tenant_id))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    meta_for_row: dict = {
        "ad_account_id": body.ad_account_id,
        "granted_scopes": granted,
        "app_id": debug.get("app_id"),
        "user_id": debug.get("user_id"),
        "token_type": debug.get("type"),
    }

    if cred is None:
        cred = ServiceCredential(
            tenant_id=store.tenant_id,
            service_type=ServiceType.TRACKING,
            service_name=ServiceName.META_MARKETING,
            credentials_encrypted=encrypted,
            encryption_key_id=key_id,
            is_active=True,
            is_validated=True,
            last_validated_at=now,
            extra_metadata=meta_for_row,
        )
        db.add(cred)
    else:
        cred.credentials_encrypted = encrypted
        cred.encryption_key_id = key_id
        cred.is_active = True
        cred.is_validated = True
        cred.last_validated_at = now
        cred.extra_metadata = meta_for_row

    settings_dict: dict = store.settings or {}
    tracking = settings_dict.get("tracking") or {}
    meta_cfg = tracking.get("meta") or {}
    meta_cfg["ad_account_id"] = body.ad_account_id
    tracking["meta"] = meta_cfg
    settings_dict["tracking"] = tracking
    store.settings = settings_dict
    flag_modified(store, "settings")

    await db.flush()
    await db.commit()

    logger.info(
        "meta_marketing_connected",
        extra={
            "store_id": str(store.id),
            "ad_account_id": body.ad_account_id,
            "granted_scopes": granted,
        },
    )

    return SuccessResponse(
        data=MarketingMetaStatusResponse(
            connected=True,
            ad_account_id=body.ad_account_id,
            granted_scopes=granted,
            last_validated_at=now,
        ),
        message="Meta Marketing connection saved",
    )


@router.delete(
    "/connect",
    response_model=SuccessResponse[MarketingMetaStatusResponse],
    summary="Disconnect Meta Marketing — soft-deletes credential row",
    operation_id="disconnect_meta_marketing",
)
async def disconnect(
    store: Annotated[Store, Depends(get_current_store)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    cred = (
        await db.execute(_get_marketing_cred_q(store.tenant_id))
    ).scalar_one_or_none()
    if cred is not None and cred.is_active:
        cred.is_active = False
        cred.is_validated = False
        await db.flush()
        await db.commit()
        logger.info(
            "meta_marketing_disconnected",
            extra={"store_id": str(store.id)},
        )
    return SuccessResponse(
        data=MarketingMetaStatusResponse(
            connected=False,
            ad_account_id=None,
            granted_scopes=None,
            last_validated_at=None,
        ),
        message="Meta Marketing disconnected",
    )
