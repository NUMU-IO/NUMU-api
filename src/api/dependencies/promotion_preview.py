"""Preview-token issuance + verification for the promotions builder.

Lets the merchant hub embed the live storefront in an iframe and render
draft / scheduled / paused promotions that aren't normally returned by
`/storefront/.../promotions/active`. The merchant requests a short-lived
JWT (5 min TTL) tied to a single store; the storefront forwards it as
`X-Preview-Token` on its server-side promotions fetch and the resolver
flips into "include drafts" mode.

The token is a JWT signed with the same key as the rest of the app's
auth tokens, so a leaked token still expires within 5 min and only
unlocks read-only preview rendering for one store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import Header
from jwt.exceptions import PyJWTError

from src.config import settings

# 5 minutes is enough for the merchant to flip between editor and the
# preview iframe a few times. Short on purpose — a stale token shouldn't
# let a curious shopper see drafts long after the merchant closed the
# builder tab.
PREVIEW_TOKEN_TTL = timedelta(minutes=5)


def issue_preview_token(*, store_id: UUID, tenant_id: UUID) -> tuple[str, datetime]:
    """Sign a preview token for `store_id`. Returns `(token, expires_at)`."""
    now = datetime.now(UTC)
    expires_at = now + PREVIEW_TOKEN_TTL
    payload = {
        "store_id": str(store_id),
        "tenant_id": str(tenant_id),
        "token_type": "promotion_preview",
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(
        payload, settings.jwt_private_key, algorithm=settings.jwt_algorithm
    )
    return token, expires_at


def decode_preview_token(token: str) -> dict | None:
    """Verify a preview token. Returns the payload dict or None on any failure.

    No exceptions on the failure path — the storefront treats a bad
    token the same as no token (live-only resolution), so a typo'd URL
    silently degrades to normal rendering rather than blowing up.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_public_key, algorithms=[settings.jwt_algorithm]
        )
    except PyJWTError:
        return None
    if payload.get("token_type") != "promotion_preview":
        return None
    return payload


async def maybe_preview_for_store(
    store_id: UUID,
    x_preview_token: str | None = Header(default=None),
) -> bool:
    """FastAPI dependency: returns True iff the request carries a valid
    preview token whose `store_id` matches the request's path param.

    Used by the storefront `/promotions/active` route to decide whether
    to surface draft / scheduled / paused promotions.
    """
    if not x_preview_token:
        return False
    payload = decode_preview_token(x_preview_token)
    if payload is None:
        return False
    return payload.get("store_id") == str(store_id)
