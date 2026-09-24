"""Partner App credentials: ``numu_app_`` tokens, client secrets, signatures.

An app token acts on ONE store with the scopes the merchant consented to.
It is checked like a PAT (same scope map, same store pin, same 300/min
bucket) with two differences, both from plan 03 §§ 5-6:

- customer conversations (``threads``, ``messages``, ``channels``,
  ``whatsapp``) need ``messages:*``; a PAT reaches them with ``marketing``;
- the store's plan does not gate it (OD-7): the install is the grant.

A token has no expiry: it lives as long as the installation, and dies on
uninstall, when the installation is disabled, when NUMU suspends the app, or
when the Partner-apps kill switch is off.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote
from uuid import UUID, uuid4

import jwt
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.app_manifest import APP_SCOPES
from src.application.services.personal_access_token_service import (
    required_scope_for,
)
from src.core.entities.app import AppStatus
from src.infrastructure.database.models.public.app import (
    AppAccessTokenModel,
    AppInstallationModel,
    AppModel,
    AppOAuthClientModel,
)

APP_TOKEN_PREFIX = "numu_app_"

#: Path segments that hold private customer conversations.
MESSAGE_SEGMENTS = frozenset({"threads", "messages", "channels", "whatsapp"})


def looks_like_app_token(token: str) -> bool:
    return token.startswith(APP_TOKEN_PREFIX)


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mint(prefix: str) -> tuple[str, str]:
    """A new secret and its sha256 hash (only the hash is stored)."""
    raw = prefix + secrets.token_urlsafe(32)
    return raw, sha256_hex(raw)


def required_app_scope(path: str, method: str) -> str | None:
    """The scope an app token needs for this request (None = never allowed).

    A scope apps may not hold (``settings:*``, ``risk:write``,
    ``themes:write``) is refused here even if an installation's grant
    somehow carries it: the manifest check alone would trust stored grants.
    """
    required = required_scope_for(path, method)
    if required is None or required == "__identity__":
        return required
    parts = path.strip("/").split("/")
    segment = parts[4] if len(parts) > 4 else ""
    if segment in MESSAGE_SEGMENTS:
        required = "messages:" + required.split(":", 1)[1]
    return required if required in APP_SCOPES else None


# ─── Client secret (readable, encrypted) ───────────────────────────


async def store_client_secret(client: AppOAuthClientModel, raw: str) -> None:
    """Hash for verification, Fernet for signing. Never plaintext."""
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    sm = get_secrets_manager()
    key_id = await sm.get_current_key_id()
    client.client_secret_hash = sha256_hex(raw)
    client.client_secret_encrypted = await sm.encrypt({"secret": raw}, key_id)
    client.secret_key_id = key_id


async def read_client_secret(db: AsyncSession, app_id: UUID) -> str | None:
    """The app's client secret, or None for an app created before Phase 4
    (it gets one on its next rotation)."""
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    client = (
        await db.execute(
            select(AppOAuthClientModel).where(AppOAuthClientModel.app_id == app_id)
        )
    ).scalar_one_or_none()
    if client is None or client.client_secret_encrypted is None:
        return None
    data = await get_secrets_manager().decrypt(
        client.client_secret_encrypted, client.secret_key_id
    )
    return data.get("secret")


def verify_client_secret(client: AppOAuthClientModel, raw: str) -> bool:
    """Timing-safe: compares the hashes, never the secrets."""
    return hmac.compare_digest(client.client_secret_hash, sha256_hex(raw or ""))


# ─── Signed query strings (plan 03 § 6.3) ──────────────────────────


def sign_params(params: dict[str, str], secret: str) -> str:
    """Lowercase hex HMAC-SHA256 of the other params, URL-decoded, sorted by
    key, joined ``k=v&k=v``. Used on the OAuth redirect and "Open app"."""
    message = "&".join(
        f"{k}={unquote(str(v))}" for k, v in sorted(params.items()) if k != "hmac"
    )
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def signed_params(params: dict[str, str], secret: str) -> dict[str, str]:
    stamped = {**params, "timestamp": str(int(datetime.now(UTC).timestamp()))}
    return {**stamped, "hmac": sign_params(stamped, secret)}


# ─── Embedded app session tokens ───────────────────────────────────

SESSION_TOKEN_ISSUER = "numueg.app"
SESSION_TOKEN_TTL = timedelta(seconds=60)


def session_token(
    secret: str, *, client_id: str, user_id: UUID, store_id: UUID, locale: str
) -> str:
    """HS256 JWT an embedded app verifies with its client secret: who (``sub``)
    is using the app on which store (``dest``), valid for 60 seconds."""
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": SESSION_TOKEN_ISSUER,
            "aud": client_id,
            "sub": str(user_id),
            "dest": str(store_id),
            "iat": now,
            "nbf": now,
            "exp": now + SESSION_TOKEN_TTL,
            "jti": uuid4().hex,
            "locale": locale,
        },
        secret,
        algorithm="HS256",
    )


# ─── Resolution ────────────────────────────────────────────────────


@dataclass(frozen=True)
class AppPrincipal:
    token: AppAccessTokenModel
    installation: AppInstallationModel
    app: AppModel


async def resolve_app_token(db: AsyncSession, raw: str) -> AppPrincipal | None:
    """The live token, or None. Checks every way a token dies."""
    from src.application.services.partner_program import partner_apps_enabled

    now = datetime.now(UTC)
    row = (
        await db.execute(
            select(AppAccessTokenModel, AppInstallationModel, AppModel)
            .join(
                AppInstallationModel,
                AppInstallationModel.id == AppAccessTokenModel.installation_id,
            )
            .join(AppModel, AppModel.id == AppInstallationModel.app_id)
            .where(
                AppAccessTokenModel.token_hash == sha256_hex(raw),
                # A future revoked_at is a rotation overlap: still valid.
                or_(
                    AppAccessTokenModel.revoked_at.is_(None),
                    AppAccessTokenModel.revoked_at > now,
                ),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    token, installation, app = row
    if not installation.is_enabled or installation.status != "active":
        return None
    if app.status == AppStatus.SUSPENDED:
        return None
    if app.developer_id is not None and not await partner_apps_enabled(db):
        return None
    return AppPrincipal(token=token, installation=installation, app=app)
