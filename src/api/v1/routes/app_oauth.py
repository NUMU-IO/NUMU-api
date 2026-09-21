"""OAuth 2.0 authorization code flow for Partner Apps (plan 03 § 6.1).

1. The hub opens its consent screen with ``client_id``, ``store_id``,
   ``scope``, ``redirect_uri`` and ``state``; it reads the consent data from
   ``GET /oauth/authorize`` (merchant session, store owner).
2. The merchant approves: ``POST /oauth/authorize/approve`` writes the
   installation (``pending_auth``) and a single-use code (10 minutes, stored
   hashed) and returns the redirect, signed with the app's client secret.
3. The app's server calls ``POST /oauth/token`` with its client secret and
   the code, and gets a ``numu_app_`` token for that one store. The
   installation becomes ``active`` and the manifest's webhook subscriptions
   are created, signed with the client secret.

Threats handled here (the Phase 4 review list): a code is single-use and
short-lived and only its hash is stored; ``redirect_uri`` must match the
manifest byte for byte, at consent and at exchange; ``state`` is required;
the client secret is compared by hash in constant time; tokens and codes are
never logged; every app URL passed the SSRF guard at submit, and deliveries
re-check it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services.app_manifest import APP_LIFECYCLE_EVENTS
from src.application.services.app_tokens import (
    APP_TOKEN_PREFIX,
    mint,
    read_client_secret,
    sha256_hex,
    signed_params,
    verify_client_secret,
)
from src.application.services.partner_program import partner_apps_enabled
from src.core.entities.app import AppStatus
from src.core.logging import get_logger
from src.infrastructure.database.models.public.app import (
    AppAccessTokenModel,
    AppInstallationModel,
    AppModel,
    AppOAuthClientModel,
    AppOAuthCodeModel,
)
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.webhook import WebhookSubscriptionModel

logger = get_logger(__name__)

router = APIRouter(prefix="/oauth", tags=["OAuth"])

CODE_TTL = timedelta(minutes=10)
#: A replaced token keeps working this long, so an app can swap without a gap.
ROTATION_OVERLAP = timedelta(hours=24)


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


# ─── Schemas ──────────────────────────────────────────────────────


class ConsentApp(BaseModel):
    slug: str
    name: dict[str, str]
    tagline: dict[str, str]
    icon: str | None
    partner: str | None
    pricing: dict | None
    privacy_policy_url: str | None


class Consent(BaseModel):
    app: ConsentApp
    store_id: UUID
    store_name: str
    scopes: list[str]
    #: What the store already granted (a re-consent shows the difference).
    granted_scopes: list[str]
    redirect_uri: str
    state: str


class ApproveRequest(BaseModel):
    client_id: str
    store_id: UUID
    scope: str = ""
    redirect_uri: str
    state: str = Field(min_length=1, max_length=512)


class TokenRequest(BaseModel):
    client_id: str
    client_secret: str
    code: str
    grant_type: str = "authorization_code"


class TokenResponse(BaseModel):
    access_token: str
    scopes: list[str]
    store_id: UUID


class RevokeRequest(BaseModel):
    client_id: str
    client_secret: str
    token: str


# ─── Helpers ──────────────────────────────────────────────────────


async def _client_app(db: AsyncSession, client_id: str) -> AppModel:
    app = await db.scalar(
        select(AppModel)
        .join(AppOAuthClientModel, AppOAuthClientModel.app_id == AppModel.id)
        .where(AppOAuthClientModel.client_id == client_id)
    )
    if app is None:
        raise _bad("unknown client_id")
    return app


async def _consentable(
    db: AsyncSession,
    *,
    user_id: UUID,
    client_id: str,
    store_id: UUID,
    scope: str,
    redirect_uri: str,
    state: str,
) -> tuple[AppModel, StoreModel, list[str]]:
    """Everything consent and approval both check, in one place."""
    if not state:
        raise _bad("state is required")
    app = await _client_app(db, client_id)
    store = await db.get(StoreModel, store_id)
    if store is None or store.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Store not found")
    if app.status == AppStatus.SUSPENDED or not await partner_apps_enabled(db):
        raise _bad("This app is not available right now.")
    if app.status != AppStatus.PUBLISHED:
        # An unpublished app installs only on its partner's own dev stores.
        plan = await db.scalar(
            select(TenantModel.plan).where(TenantModel.id == store.tenant_id)
        )
        if app.developer_id != user_id or plan != "developer":
            raise _bad("This app is not published yet.")
    contract = (app.manifest or {}).get("app") or {}
    oauth = contract.get("oauth") or {}
    if redirect_uri not in (oauth.get("redirect_urls") or []):
        # Byte-for-byte: no prefix or normalisation matching.
        raise _bad("redirect_uri does not match the app's registered redirect URLs")
    required = set(oauth.get("scopes") or [])
    allowed = required | set(oauth.get("optional_scopes") or [])
    asked = {s for s in scope.replace(",", " ").split() if s} or required
    if not asked <= allowed:
        raise _bad(
            f"scope not declared by the app: {', '.join(sorted(asked - allowed))}"
        )
    if not required <= asked:
        raise _bad(f"missing required scopes: {', '.join(sorted(required - asked))}")
    return app, store, sorted(asked)


# ─── Consent ──────────────────────────────────────────────────────


@router.get("/authorize", response_model=SuccessResponse[Consent])
async def authorize(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    client_id: str = Query(...),
    store_id: UUID = Query(...),
    redirect_uri: str = Query(...),
    state: str = Query(""),
    scope: str = Query(""),
):
    """What the consent screen shows. Changes nothing."""
    app, store, scopes = await _consentable(
        db,
        user_id=user_id,
        client_id=client_id,
        store_id=store_id,
        scope=scope,
        redirect_uri=redirect_uri,
        state=state,
    )
    m = app.manifest or {}
    locales = m.get("app_locales") or {}
    installed = await db.scalar(
        select(AppInstallationModel).where(
            AppInstallationModel.app_id == app.id,
            AppInstallationModel.store_id == store.id,
        )
    )
    partner = await db.scalar(
        select(PartnerAccountModel.display_name).where(
            PartnerAccountModel.user_id == app.developer_id
        )
    )
    return SuccessResponse(
        data=Consent(
            app=ConsentApp(
                slug=app.slug,
                name={k: v.get("name", "") for k, v in locales.items()},
                tagline={
                    k: v.get("tagline", "") for k, v in (m.get("locales") or {}).items()
                },
                icon=app.icon_url,
                partner=partner,
                pricing=m.get("pricing"),
                privacy_policy_url=(m.get("app") or {}).get("privacy_policy_url"),
            ),
            store_id=store.id,
            store_name=store.name,
            scopes=scopes,
            granted_scopes=list(installed.granted_scopes) if installed else [],
            redirect_uri=redirect_uri,
            state=state,
        )
    )


@router.post("/authorize/approve", response_model=SuccessResponse[dict])
async def approve(
    body: ApproveRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """The merchant said yes: a code, and the signed redirect to the app."""
    app, store, scopes = await _consentable(
        db,
        user_id=user_id,
        client_id=body.client_id,
        store_id=body.store_id,
        scope=body.scope,
        redirect_uri=body.redirect_uri,
        state=body.state,
    )
    secret = await read_client_secret(db, app.id)
    if not secret:
        # Created before Phase 4: the partner must rotate the secret once.
        raise HTTPException(
            status_code=409, detail="This app must rotate its client secret."
        )

    await db.execute(
        pg_insert(AppInstallationModel)
        .values(
            tenant_id=store.tenant_id,
            store_id=store.id,
            app_id=app.id,
            is_enabled=True,
            settings={},
            status="pending_auth",
            granted_scopes=[],
        )
        .on_conflict_do_update(
            # Re-consent on a live install keeps it live (and its current
            # token) until the app exchanges the new code.
            constraint="uq_app_installation_store_app",
            set_={"is_enabled": True},
        )
    )
    installation = await db.scalar(
        select(AppInstallationModel).where(
            AppInstallationModel.app_id == app.id,
            AppInstallationModel.store_id == store.id,
        )
    )
    code, code_hash = mint("numu_code_")
    db.add(
        AppOAuthCodeModel(
            installation_id=installation.id,
            code_hash=code_hash,
            redirect_uri=body.redirect_uri,
            scopes=scopes,
            expires_at=datetime.now(UTC) + CODE_TTL,
        )
    )
    await db.flush()
    params = signed_params(
        {"code": code, "store_id": str(store.id), "state": body.state}, secret
    )
    logger.info("oauth_consent_approved", app=app.slug, store_id=str(store.id))
    return SuccessResponse(
        data={"redirect_url": f"{body.redirect_uri}?{urlencode(params)}"}
    )


# ─── Token ────────────────────────────────────────────────────────


@router.post("/token", response_model=SuccessResponse[TokenResponse])
async def token(body: TokenRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    """The app's server exchanges a code for a ``numu_app_`` token."""
    if body.grant_type != "authorization_code":
        raise _bad("grant_type must be authorization_code")
    client = await db.scalar(
        select(AppOAuthClientModel).where(
            AppOAuthClientModel.client_id == body.client_id
        )
    )
    if client is None or not verify_client_secret(client, body.client_secret):
        raise HTTPException(status_code=401, detail="invalid client credentials")
    now = datetime.now(UTC)
    # Single use, atomically: two racing exchanges cannot both win.
    code = await db.scalar(
        update(AppOAuthCodeModel)
        .where(
            AppOAuthCodeModel.code_hash == sha256_hex(body.code),
            AppOAuthCodeModel.used_at.is_(None),
            AppOAuthCodeModel.expires_at > now,
        )
        .values(used_at=now)
        .returning(AppOAuthCodeModel)
    )
    installation = (
        await db.get(AppInstallationModel, code.installation_id) if code else None
    )
    if code is None or installation is None or installation.app_id != client.app_id:
        raise _bad("invalid, expired or already used code")
    app = await db.get(AppModel, client.app_id)

    # One live token per installation; the old one overlaps for 24 hours.
    await db.execute(
        update(AppAccessTokenModel)
        .where(
            AppAccessTokenModel.installation_id == installation.id,
            AppAccessTokenModel.revoked_at.is_(None),
        )
        .values(revoked_at=now + ROTATION_OVERLAP)
    )
    raw, token_hash = mint(APP_TOKEN_PREFIX)
    db.add(
        AppAccessTokenModel(
            installation_id=installation.id, token_hash=token_hash, scopes=code.scopes
        )
    )
    installation.status = "active"
    installation.granted_scopes = code.scopes

    # The manifest's subscriptions, owned by this installation. Lifecycle
    # events are delivered directly (app_webhooks) and need none.
    await db.execute(
        WebhookSubscriptionModel.__table__.delete().where(
            WebhookSubscriptionModel.app_installation_id == installation.id
        )
    )
    by_url: dict[str, list[str]] = {}
    for hook in ((app.manifest or {}).get("app") or {}).get("webhooks") or []:
        if hook["event"] not in APP_LIFECYCLE_EVENTS:
            by_url.setdefault(hook["url"], []).append(hook["event"])
    for url, events in by_url.items():
        db.add(
            WebhookSubscriptionModel(
                tenant_id=installation.tenant_id,
                store_id=installation.store_id,
                url=url,
                events=sorted(set(events)),
                secret="",  # signed with the app's client secret at send time
                is_active=True,
                description=f"app:{app.slug}",
                app_installation_id=installation.id,
            )
        )
    await db.flush()
    logger.info("oauth_token_issued", app=app.slug, store_id=str(installation.store_id))
    return SuccessResponse(
        data=TokenResponse(
            access_token=raw, scopes=code.scopes, store_id=installation.store_id
        )
    )


@router.post("/revoke", response_model=SuccessResponse[dict])
async def revoke(body: RevokeRequest, db: Annotated[AsyncSession, Depends(get_db)]):
    """The app revokes one of its tokens. Answers 200 for an unknown token
    (RFC 7009), so the endpoint cannot probe for valid tokens."""
    client = await db.scalar(
        select(AppOAuthClientModel).where(
            AppOAuthClientModel.client_id == body.client_id
        )
    )
    if client is None or not verify_client_secret(client, body.client_secret):
        raise HTTPException(status_code=401, detail="invalid client credentials")
    token_row = await db.scalar(
        select(AppAccessTokenModel)
        .join(
            AppInstallationModel,
            AppInstallationModel.id == AppAccessTokenModel.installation_id,
        )
        .where(
            AppAccessTokenModel.token_hash == sha256_hex(body.token),
            AppInstallationModel.app_id == client.app_id,
        )
    )
    if token_row is not None:
        token_row.revoked_at = datetime.now(UTC)
    return SuccessResponse(data={"revoked": True})
