from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import Response

from src.api.v1.routes.auth import _revoke_other_sessions


def _call_parts(cookies, pat=None):
    request = SimpleNamespace(cookies=cookies, state=SimpleNamespace(pat=pat))
    payload = SimpleNamespace(
        user_id=uuid4(), tenant_id=None, membership_id=None, perm_version=0
    )
    user_repo = MagicMock(get_by_id=AsyncMock(return_value=object()))
    token_service = MagicMock(
        create_access_token=MagicMock(return_value="access"),
        create_refresh_token=MagicMock(return_value="refresh"),
    )
    revocation = MagicMock(revoke_all=AsyncMock())
    return request, Response(), payload, user_repo, token_service, revocation


@pytest.mark.asyncio
async def test_cookie_session_is_revoked_then_reissued():
    request, response, payload, users, tokens, revocation = _call_parts({
        "refresh_token": "r"
    })

    await _revoke_other_sessions(request, response, payload, users, tokens, revocation)

    revocation.revoke_all.assert_awaited_once()
    cookies = response.headers.getlist("set-cookie")
    assert any(c.startswith("access_token=access") for c in cookies)
    assert any(c.startswith("refresh_token=refresh") for c in cookies)


@pytest.mark.asyncio
async def test_access_token_caller_is_revoked_but_never_given_a_session():
    request, response, payload, users, tokens, revocation = _call_parts(
        {"refresh_token": "r"}, pat={"token_id": "t"}
    )

    await _revoke_other_sessions(request, response, payload, users, tokens, revocation)

    revocation.revoke_all.assert_awaited_once()
    assert response.headers.getlist("set-cookie") == []
    tokens.create_refresh_token.assert_not_called()
