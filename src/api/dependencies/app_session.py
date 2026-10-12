"""``require_app_session("<slug>")``: who is calling one app's own routes.

An embedded app's front (APP-STANDARD § 4.2) proves itself with the hub's
60-second session token (``app_tokens.session_token``): HS256, signed with the
app's client secret, ``aud`` = client id, ``sub`` = user, ``dest`` = store. The
front sends it as ``Authorization: Bearer``. This dependency is the only place
NUMU-api accepts one, and the routes under ``/api/v1/apps/<slug>`` accept
nothing else: a merchant JWT or a ``numu_app_`` token is refused here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import apply_rls_tenant
from src.api.dependencies.database import get_db
from src.application.services.app_install_gate import live_installs
from src.application.services.app_tokens import (
    SESSION_TOKEN_ISSUER,
    read_client_secret,
)
from src.infrastructure.database.models.public.app import (
    AppModel,
    AppOAuthClientModel,
)
from src.infrastructure.repositories.store_repository import StoreRepository

#: Clock skew tolerated between the hub that minted a token and this API.
LEEWAY_SECONDS = 30


@dataclass(frozen=True)
class AppSession:
    store_id: UUID
    user_id: UUID
    app_id: UUID
    locale: str


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid app session token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@cache
def require_app_session(slug: str):
    """The dependency for one app's routes.

    Cached per slug, so FastAPI runs it once per request however many places
    declare it. The function carries ``app_session_slug`` so a test can prove
    that every route under ``/api/v1/apps/<slug>`` depends on it.
    """

    async def app_session(
        request: Request, db: Annotated[AsyncSession, Depends(get_db)]
    ) -> AppSession:
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _unauthorized()
        try:
            # Only to find which app's secret verifies it; checked below.
            client_id = jwt.decode(token, options={"verify_signature": False}).get(
                "aud"
            )
        except jwt.PyJWTError:
            raise _unauthorized() from None
        if not isinstance(client_id, str):
            raise _unauthorized()
        app = (
            await db.execute(
                select(AppModel)
                .join(AppOAuthClientModel, AppOAuthClientModel.app_id == AppModel.id)
                .where(AppOAuthClientModel.client_id == client_id)
            )
        ).scalar_one_or_none()
        if app is None or app.slug != slug:
            raise _unauthorized()
        secret = await read_client_secret(db, app.id)
        if not secret:
            raise _unauthorized()
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=client_id,
                issuer=SESSION_TOKEN_ISSUER,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "nbf", "iss", "aud", "sub", "dest"]},
            )
            store_id, user_id = UUID(claims["dest"]), UUID(claims["sub"])
        except (jwt.PyJWTError, ValueError):
            raise _unauthorized() from None

        store = await StoreRepository(db).get_by_id(store_id)
        if store is None or store.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this store",
            )
        live = await live_installs(db, store_id)
        if (await db.execute(live.where(AppModel.id == app.id))).first() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This app is not installed on this store.",
            )
        await apply_rls_tenant(db, store)
        return AppSession(
            store_id=store_id,
            user_id=user_id,
            app_id=app.id,
            locale="en" if claims.get("locale") == "en" else "ar",
        )

    app_session.app_session_slug = slug  # type: ignore[attr-defined]
    return app_session


def app_router(slug: str) -> APIRouter:
    """A router for one app's routes, every one behind its session token.

    Lives here, not in ``routes/apps/__init__.py``, so an app's route module
    and the package that includes it never import each other.
    """
    return APIRouter(
        prefix=f"/{slug}",
        tags=[f"App: {slug}"],
        dependencies=[Depends(require_app_session(slug))],
    )
