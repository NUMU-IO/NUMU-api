"""Only access tokens open a session.

Refresh (30-day), password-reset and email-verification tokens are signed
with the same key and used to pass as access tokens, and an impersonation
token could be swapped at ``/auth/token-handoff`` for the owner's own
month-long session.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from src.api.dependencies import auth as auth_deps
from src.api.v1.routes import auth as auth_routes
from src.core.entities.user import User, UserRole, UserStatus
from src.core.value_objects.email import Email
from src.infrastructure.external_services.token_service import TokenService

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE = _key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption(),
).decode()
PUBLIC = (
    _key
    .public_key()
    .public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    .decode()
)
TOKENS = TokenService(private_key=PRIVATE, public_key=PUBLIC, algorithm="RS256")
USER = User(
    id=uuid4(),
    email=Email(value="owner@example.com"),
    hashed_password="x",
    first_name="O",
    last_name="W",
    role=UserRole.STORE_OWNER,
    status=UserStatus.ACTIVE,
)


class _NotRevoked:
    async def is_revoked(self, *_args):
        return False


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    monkeypatch.setattr(auth_deps, "token_service", TOKENS)
    monkeypatch.setattr(auth_deps, "_revocation_service", _NotRevoked())
    monkeypatch.setattr(auth_routes, "TokenRevocationService", lambda _c: _NotRevoked())
    monkeypatch.setattr(auth_routes, "RedisCacheService", lambda: None)


def _bearer(token: str):
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}"}, cookies={}, state=None
    )


def _untyped_token() -> str:
    now = datetime.utcnow()
    claims = {"sub": str(USER.id), "email": "o@example.com", "role": "store_owner"}
    claims |= {"iat": now, "exp": now + timedelta(minutes=5)}
    return jwt.encode(claims, PRIVATE, algorithm="RS256")


@pytest.mark.asyncio
async def test_an_access_token_resolves():
    payload = await auth_deps._resolve_principal(
        _bearer(TOKENS.create_access_token(USER))
    )
    assert payload.user_id == USER.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mint",
    [
        TOKENS.create_refresh_token,
        TOKENS.create_reset_token,
        TOKENS.create_email_verification_token,
        lambda _user: _untyped_token(),
    ],
)
async def test_other_token_types_are_not_sessions(mint):
    with pytest.raises(HTTPException) as exc:
        await auth_deps._resolve_principal(_bearer(mint(USER)))
    assert exc.value.status_code == 401

    request = SimpleNamespace(cookies={"admin_access_token": mint(USER)})
    with pytest.raises(HTTPException) as exc:
        await auth_deps._get_admin_user_role(request)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_impersonation_token_cannot_be_handed_off():
    token = TOKENS.create_access_token(USER, impersonated_by=uuid4())
    body = SimpleNamespace(access_token=token, refresh_token="")

    with pytest.raises(HTTPException) as exc:
        await auth_routes.token_handoff(
            body, None, user_repo=None, token_service=TOKENS
        )

    assert exc.value.status_code == 401
    # Still a working Bearer for the impersonation tab itself.
    assert (await auth_deps._resolve_principal(_bearer(token))).user_id == USER.id
