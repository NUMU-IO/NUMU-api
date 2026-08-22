"""Unit tests for authentication use cases."""

import time
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.dto.auth import (
    LoginDTO,
    PasswordResetRequestDTO,
    RefreshTokenDTO,
    RegisterDTO,
)
from src.application.use_cases.auth.forgot_password import ForgotPasswordUseCase
from src.application.use_cases.auth.login import LoginUserUseCase
from src.application.use_cases.auth.refresh_token import RefreshTokenUseCase
from src.application.use_cases.auth.register import RegisterUserUseCase
from src.core.entities.user import User, UserRole, UserStatus
from src.core.exceptions import (
    EntityNotFoundError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from src.core.value_objects.email import Email


class TestRegisterUserUseCase:
    """Tests for RegisterUserUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_user_repo = MagicMock()
        self.mock_user_repo.get_by_email_str = AsyncMock(return_value=None)
        self.mock_user_repo.email_exists = AsyncMock(return_value=False)
        self.mock_user_repo.create = AsyncMock()

        self.mock_password_service = MagicMock()
        self.mock_password_service.hash_password = MagicMock(
            return_value="hashed_password"
        )

        self.mock_token_service = MagicMock()
        self.mock_token_service.create_access_token = MagicMock(
            return_value="access_token"
        )
        self.mock_token_service.create_refresh_token = MagicMock(
            return_value="refresh_token"
        )

        self.use_case = RegisterUserUseCase(
            user_repository=self.mock_user_repo,
            password_service=self.mock_password_service,
            token_service=self.mock_token_service,
        )

    @pytest.mark.asyncio
    async def test_register_success(self):
        """Test successful user registration."""
        user_id = uuid4()
        created_user = User(
            id=user_id,
            email=Email(value="newuser@example.com"),
            hashed_password="hashed_password",
            first_name="New",
            last_name="User",
            role=UserRole.STORE_OWNER,
            status=UserStatus.ACTIVE,
        )
        self.mock_user_repo.create.return_value = created_user

        dto = RegisterDTO(
            email="newuser@example.com",
            password="SecurePassword123!",
            first_name="New",
            last_name="User",
        )

        result = await self.use_case.execute(dto)

        assert result is not None
        assert result.user.email == "newuser@example.com"
        assert result.tokens.access_token == "access_token"
        assert result.tokens.refresh_token == "refresh_token"
        self.mock_password_service.hash_password.assert_called_once_with(
            "SecurePassword123!"
        )

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self):
        """Test registration with existing email."""
        # Mock email_exists to return True (duplicate email)
        self.mock_user_repo.email_exists.return_value = True

        dto = RegisterDTO(
            email="existing@example.com",
            password="password123",
            first_name="New",
            last_name="User",
        )

        with pytest.raises(Exception):
            await self.use_case.execute(dto)


class TestLoginUserUseCase:
    """Tests for LoginUserUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.user_id = uuid4()
        self.sample_user = User(
            id=self.user_id,
            email=Email(value="user@example.com"),
            hashed_password="hashed_password",
            first_name="Test",
            last_name="User",
            role=UserRole.STORE_OWNER,
            status=UserStatus.ACTIVE,
        )

        self.mock_user_repo = MagicMock()
        self.mock_user_repo.get_by_email_str = AsyncMock(return_value=self.sample_user)
        self.mock_user_repo.update = AsyncMock(return_value=self.sample_user)

        self.mock_password_service = MagicMock()
        self.mock_password_service.verify_password = MagicMock(return_value=True)

        self.mock_token_service = MagicMock()
        self.mock_token_service.create_access_token = MagicMock(
            return_value="access_token"
        )
        self.mock_token_service.create_refresh_token = MagicMock(
            return_value="refresh_token"
        )

        # Account lockout (C-2): not locked, so the credential path runs.
        self.mock_lockout_service = MagicMock()
        self.mock_lockout_service.check_locked = AsyncMock(return_value=(False, None))
        self.mock_lockout_service.record_failure = AsyncMock()
        self.mock_lockout_service.clear = AsyncMock()

        self.use_case = LoginUserUseCase(
            user_repository=self.mock_user_repo,
            password_service=self.mock_password_service,
            token_service=self.mock_token_service,
            lockout_service=self.mock_lockout_service,
        )

    @pytest.mark.asyncio
    async def test_login_success(self):
        """Test successful login."""
        dto = LoginDTO(
            email="user@example.com",
            password="correct_password",
        )

        result = await self.use_case.execute(dto)

        assert result is not None
        assert result.user.email == "user@example.com"
        assert result.tokens.access_token == "access_token"
        self.mock_password_service.verify_password.assert_called_once()

    @pytest.mark.asyncio
    async def test_login_user_not_found(self):
        """Test login with non-existent user."""
        self.mock_user_repo.get_by_email_str.return_value = None

        dto = LoginDTO(
            email="nonexistent@example.com",
            password="password",
        )

        with pytest.raises(InvalidCredentialsError):
            await self.use_case.execute(dto)

    @pytest.mark.asyncio
    async def test_login_wrong_password(self):
        """Test login with wrong password."""
        self.mock_password_service.verify_password.return_value = False

        dto = LoginDTO(
            email="user@example.com",
            password="wrong_password",
        )

        with pytest.raises(InvalidCredentialsError):
            await self.use_case.execute(dto)


class TestRefreshTokenUseCase:
    """Tests for RefreshTokenUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.user_id = uuid4()
        self.sample_user = User(
            id=self.user_id,
            email=Email(value="user@example.com"),
            hashed_password="hashed",
            first_name="Test",
            last_name="User",
            role=UserRole.STORE_OWNER,
            status=UserStatus.ACTIVE,
        )

        self.mock_user_repo = MagicMock()
        self.mock_user_repo.get_by_id = AsyncMock(return_value=self.sample_user)

        # Create a mock payload object
        self.mock_payload = MagicMock()
        self.mock_payload.token_type = "refresh"
        self.mock_payload.user_id = self.user_id
        self.mock_payload.jti = "jti-123"
        self.mock_payload.exp = 9999999999

        # Rotation blacklist: this jti has not been consumed yet.
        self.mock_blacklist_service = MagicMock()
        self.mock_blacklist_service.is_used = AsyncMock(return_value=False)
        self.mock_blacklist_service.mark_used = AsyncMock()
        self.mock_blacklist_service.get_rotation = AsyncMock(return_value=None)
        self.mock_blacklist_service.remember_rotation = AsyncMock()
        self.mock_blacklist_service.is_family_revoked = AsyncMock(return_value=False)
        self.mock_blacklist_service.revoke_family = AsyncMock()
        self.mock_payload.family_id = "fam-1"
        self.mock_payload.tenant_id = None
        self.mock_payload.membership_id = None
        self.mock_payload.perm_version = 0

        self.mock_token_service = MagicMock()
        self.mock_token_service.verify_token = MagicMock(return_value=self.mock_payload)
        self.mock_token_service.create_access_token = MagicMock(
            return_value="new_access_token"
        )
        self.mock_token_service.create_refresh_token = MagicMock(
            return_value="new_refresh_token"
        )

        self.use_case = RefreshTokenUseCase(
            user_repository=self.mock_user_repo,
            token_service=self.mock_token_service,
            blacklist_service=self.mock_blacklist_service,
        )

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        """Test successful token refresh."""
        dto = RefreshTokenDTO(refresh_token="valid_refresh_token")

        result = await self.use_case.execute(dto)

        assert result is not None
        assert result.access_token == "new_access_token"
        assert result.refresh_token == "new_refresh_token"
        self.mock_token_service.verify_token.assert_called_once_with(
            "valid_refresh_token"
        )

    @pytest.mark.asyncio
    async def test_refresh_token_user_not_found(self):
        """Test refresh when user no longer exists."""
        self.mock_user_repo.get_by_id.return_value = None

        dto = RefreshTokenDTO(refresh_token="valid_token")

        with pytest.raises(EntityNotFoundError):
            await self.use_case.execute(dto)

    @pytest.mark.asyncio
    async def test_refresh_carries_claims_and_remembers_rotation(self):
        """Tenant/membership claims survive rotation; the pair is cached
        under the consumed jti for the grace window."""
        from uuid import uuid4

        tenant_id, membership_id = uuid4(), uuid4()
        self.mock_payload.tenant_id = tenant_id
        self.mock_payload.membership_id = membership_id
        self.mock_payload.perm_version = 3

        await self.use_case.execute(RefreshTokenDTO(refresh_token="t"))

        for factory in (
            self.mock_token_service.create_access_token,
            self.mock_token_service.create_refresh_token,
        ):
            kwargs = factory.call_args.kwargs
            assert kwargs["tenant_id"] == tenant_id
            assert kwargs["membership_id"] == membership_id
            assert kwargs["perm_version"] == 3
        self.mock_blacklist_service.mark_used.assert_awaited_once_with(
            "jti-123", 9999999999
        )
        self.mock_blacklist_service.remember_rotation.assert_awaited_once()
        args = self.mock_blacklist_service.remember_rotation.call_args
        assert args.args[:3] == ("jti-123", "new_access_token", "new_refresh_token")
        assert args.kwargs["ttl"] > 0

    @pytest.mark.asyncio
    async def test_refresh_second_tab_inside_grace_gets_same_pair(self):
        """A consumed jti presented again within the grace window is NOT
        theft — it's the other tab. Same pair, no 401."""
        self.mock_blacklist_service.is_used.return_value = True
        self.mock_blacklist_service.get_rotation.return_value = (
            "graced_access",
            "graced_refresh",
        )

        result = await self.use_case.execute(RefreshTokenDTO(refresh_token="t"))

        assert (result.access_token, result.refresh_token) == (
            "graced_access",
            "graced_refresh",
        )
        self.mock_token_service.create_access_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_reuse_after_grace_is_rejected_and_revokes_family(self):
        self.mock_blacklist_service.is_used.return_value = True
        self.mock_blacklist_service.get_rotation.return_value = None

        with pytest.raises(InvalidTokenError):
            await self.use_case.execute(RefreshTokenDTO(refresh_token="t"))
        self.mock_blacklist_service.revoke_family.assert_awaited_once()
        assert self.mock_blacklist_service.revoke_family.call_args.args[0] == "fam-1"

    @pytest.mark.asyncio
    async def test_refresh_rejects_revoked_family_even_with_fresh_jti(self):
        self.mock_blacklist_service.is_family_revoked.return_value = True

        with pytest.raises(InvalidTokenError):
            await self.use_case.execute(RefreshTokenDTO(refresh_token="t"))
        self.mock_token_service.create_access_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_carries_family_id_into_new_refresh_token(self):
        await self.use_case.execute(RefreshTokenDTO(refresh_token="t"))
        kwargs = self.mock_token_service.create_refresh_token.call_args.kwargs
        assert kwargs["family_id"] == "fam-1"


class TestForgotPasswordUseCase:
    """Tests for ForgotPasswordUseCase — C-17 timing attack fix."""

    def setup_method(self):
        self.mock_user_repo = MagicMock()
        self.mock_user_repo.get_by_email = AsyncMock(return_value=None)

        self.mock_token_service = MagicMock()
        self.mock_token_service.create_reset_token = MagicMock(return_value="tok")

        self.mock_email_service = MagicMock()
        self.mock_email_service.send_password_reset_email = AsyncMock()

        self.use_case = ForgotPasswordUseCase(
            user_repository=self.mock_user_repo,
            token_service=self.mock_token_service,
            email_service=self.mock_email_service,
        )

    @pytest.mark.asyncio
    async def test_nonexistent_user_takes_at_least_2_seconds(self):
        """Response time must not reveal whether the email exists."""
        dto = PasswordResetRequestDTO(email="ghost@example.com")

        start = time.monotonic()
        await self.use_case.execute(dto)
        elapsed = time.monotonic() - start

        assert elapsed >= 2.0, (
            f"Forgot-password for unknown user returned in {elapsed:.2f}s; "
            "expected >= 2s to prevent timing attacks"
        )

    @pytest.mark.asyncio
    async def test_existing_user_takes_at_least_2_seconds(self):
        """Even when the user exists, the minimum delay is enforced."""
        user = User(
            id=uuid4(),
            email=Email(value="real@example.com"),
            hashed_password="hashed",
            first_name="A",
            last_name="B",
            role=UserRole.STORE_OWNER,
            status=UserStatus.ACTIVE,
        )
        self.mock_user_repo.get_by_email.return_value = user

        dto = PasswordResetRequestDTO(email="real@example.com")

        start = time.monotonic()
        await self.use_case.execute(dto)
        elapsed = time.monotonic() - start

        assert elapsed >= 2.0
        self.mock_email_service.send_password_reset_email.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_email_sent_for_nonexistent_user(self):
        """No token or email should be generated for unknown emails."""
        dto = PasswordResetRequestDTO(email="nobody@example.com")

        await self.use_case.execute(dto)

        self.mock_token_service.create_reset_token.assert_not_called()
        self.mock_email_service.send_password_reset_email.assert_not_called()
