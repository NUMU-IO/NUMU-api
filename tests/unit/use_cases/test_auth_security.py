"""Security appendix of the onboarding audit: account takeover via Google,
sessions surviving a password reset, the resend cooldown, plaintext store
passwords, and password checks on accounts that have none."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.application.dto.auth import PasswordResetDTO
from src.application.use_cases.auth import google_oauth
from src.application.use_cases.auth.change_password import (
    ChangePasswordDTO,
    ChangePasswordUseCase,
)
from src.application.use_cases.auth.google_oauth import GoogleOAuthUseCase
from src.application.use_cases.auth.reset_password import ResetPasswordUseCase
from src.core.entities.user import User, UserRole, UserStatus
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.external_services.password_service import PasswordService

pw = PasswordService()


def _user(*, verified: bool) -> User:
    return User(
        id=uuid4(),
        email=Email(value="owner@example.com"),
        hashed_password=pw.hash_password("attacker-chose-this"),
        first_name="Attacker",
        last_name="Name",
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE if verified else UserStatus.PENDING_VERIFICATION,
        phone=PhoneNumber(value="+201000000000"),
        email_verified_at=datetime.now(UTC) if verified else None,
    )


def _google_use_case(user: User, monkeypatch):
    monkeypatch.setattr(google_oauth.settings, "google_oauth_client_id", "cid")
    monkeypatch.setattr(
        google_oauth.google_id_token,
        "verify_oauth2_token",
        lambda *a, **k: {
            "sub": "google-sub",
            "email": "owner@example.com",
            "email_verified": True,
            "given_name": "Real",
            "family_name": "Owner",
        },
    )
    repo = MagicMock()
    repo.get_by_google_id = AsyncMock(return_value=None)
    repo.get_by_email_str = AsyncMock(return_value=user)
    repo.update = AsyncMock(side_effect=lambda u: u)
    tokens = MagicMock()
    tokens.create_access_token.return_value = "a"
    tokens.create_refresh_token.return_value = "r"
    revocation = MagicMock(revoke_all=AsyncMock())
    two_factor = MagicMock(delete_by_user_id=AsyncMock())
    return (
        GoogleOAuthUseCase(repo, tokens, revocation, two_factor),
        revocation,
        two_factor,
    )


async def test_google_reclaims_an_unverified_registration(monkeypatch):
    user = _user(verified=False)
    use_case, revocation, two_factor = _google_use_case(user, monkeypatch)

    await use_case.execute("token")

    assert use_case.reclaimed
    assert not pw.verify_password("attacker-chose-this", user.hashed_password)
    assert user.phone is None and user.first_name == "Real"
    assert user.is_verified and user.google_id == "google-sub"
    revocation.revoke_all.assert_awaited_once()
    two_factor.delete_by_user_id.assert_awaited_once_with(user.id)


async def test_google_links_a_verified_account_without_wiping_it(monkeypatch):
    user = _user(verified=True)
    use_case, revocation, two_factor = _google_use_case(user, monkeypatch)

    await use_case.execute("token")

    assert not use_case.reclaimed
    assert pw.verify_password("attacker-chose-this", user.hashed_password)
    revocation.revoke_all.assert_not_awaited()
    two_factor.delete_by_user_id.assert_not_awaited()


async def test_google_sign_in_still_asks_for_the_second_factor(monkeypatch):
    from src.api.v1.routes import auth as auth_routes

    uid = uuid4()

    class FakeUseCase:
        def __init__(self, **_kwargs):
            self.reclaimed = False

        async def execute(self, *_a, **_k):
            return SimpleNamespace(
                user=SimpleNamespace(
                    id=uid,
                    email="owner@example.com",
                    first_name="R",
                    last_name="O",
                    phone=None,
                    language="ar",
                ),
                tokens=SimpleNamespace(access_token="a", refresh_token="r"),
            )

    monkeypatch.setattr(google_oauth, "GoogleOAuthUseCase", FakeUseCase)
    monkeypatch.setattr(
        "src.application.services.signup_settings.get_signup_settings",
        AsyncMock(return_value=SimpleNamespace(trial_days=37)),
    )
    monkeypatch.setattr(
        "src.application.services.merchant_leads.record_lead", AsyncMock()
    )
    request = MagicMock(json=AsyncMock(return_value={"id_token": "t"}))
    response = MagicMock()
    tokens = MagicMock(create_challenge_token=MagicMock(return_value="challenge"))
    two_factor = MagicMock(user_has_2fa_enabled=AsyncMock(return_value=True))
    db = MagicMock(execute=AsyncMock(), commit=AsyncMock())

    result = await auth_routes.google_oauth(
        request, response, MagicMock(), tokens, two_factor, db
    )

    assert result.data.requires_2fa and result.data.challenge_token == "challenge"
    assert result.data.tokens is None
    response.set_cookie.assert_not_called()


async def test_password_reset_signs_out_every_session():
    user = _user(verified=True)
    repo = MagicMock(
        get_by_id=AsyncMock(return_value=user), update=AsyncMock(return_value=user)
    )
    tokens = MagicMock()
    tokens.verify_token.return_value = SimpleNamespace(
        token_type="reset", user_id=user.id
    )
    revocation = MagicMock(revoke_all=AsyncMock())

    await ResetPasswordUseCase(repo, tokens, pw, revocation).execute(
        PasswordResetDTO(token="t", new_password="a long new passphrase")
    )

    revocation.revoke_all.assert_awaited_once()
    assert revocation.revoke_all.await_args.args[0] == user.id


async def test_change_password_works_and_signs_out():
    user = _user(verified=True)
    repo = MagicMock(
        get_by_id=AsyncMock(return_value=user), update=AsyncMock(return_value=user)
    )
    revocation = MagicMock(revoke_all=AsyncMock())

    await ChangePasswordUseCase(repo, pw, revocation).execute(
        user.id,
        ChangePasswordDTO(
            current_password="attacker-chose-this",
            new_password="a long new passphrase",
        ),
    )

    assert pw.verify_password("a long new passphrase", user.hashed_password)
    revocation.revoke_all.assert_awaited_once()


def test_an_account_without_a_password_never_matches():
    assert pw.verify_password("anything", "") is False


async def test_resend_verification_has_a_server_side_cooldown(monkeypatch):
    from src.api.v1.routes import auth as auth_routes

    store: dict = {}

    class FakeCache:
        async def set_if_absent(self, key, value, expire=None):
            if key in store:
                return False
            store[key] = value
            return True

        async def exists(self, key):
            return key in store

        async def set(self, key, value, expire=None):
            store[key] = value

        async def delete(self, key):
            store.pop(key, None)

    monkeypatch.setattr(auth_routes, "RedisCacheService", FakeCache)
    user = _user(verified=False)
    repo = MagicMock(get_by_id=AsyncMock(return_value=user))
    tokens = MagicMock(create_email_verification_token=MagicMock(return_value="t"))
    email = MagicMock(send_verification_email=AsyncMock())

    await auth_routes.resend_verification(str(user.id), repo, tokens, email)
    with pytest.raises(HTTPException) as exc:
        await auth_routes.resend_verification(str(user.id), repo, tokens, email)

    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "RATE_LIMIT_EXCEEDED"
    email.send_verification_email.assert_awaited_once()


def test_store_settings_never_store_a_plaintext_storefront_password():
    from src.api.v1.schemas.tenant.store import UpdateStoreRequest

    req = UpdateStoreRequest(settings={"storefront_password": "1234", "a": 1})
    assert req.settings == {"a": 1}
