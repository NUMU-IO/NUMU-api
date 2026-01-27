"""Unit tests for authentication use cases."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.application.dto.auth import LoginDTO, RegisterDTO, RefreshTokenDTO
from src.application.use_cases.auth.login import LoginUserUseCase
from src.application.use_cases.auth.register import RegisterUserUseCase
from src.application.use_cases.auth.refresh_token import RefreshTokenUseCase
from src.core.entities.user import User, UserRole, UserStatus
from src.core.exceptions import AuthenticationError, InvalidCredentialsError, EntityNotFoundError
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
        self.mock_password_service.hash_password = MagicMock(return_value="hashed_password")

        self.mock_token_service = MagicMock()
        self.mock_token_service.create_access_token = MagicMock(return_value="access_token")
        self.mock_token_service.create_refresh_token = MagicMock(return_value="refresh_token")

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
        self.mock_password_service.hash_password.assert_called_once_with("SecurePassword123!")

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
        self.mock_token_service.create_access_token = MagicMock(return_value="access_token")
        self.mock_token_service.create_refresh_token = MagicMock(return_value="refresh_token")

        self.use_case = LoginUserUseCase(
            user_repository=self.mock_user_repo,
            password_service=self.mock_password_service,
            token_service=self.mock_token_service,
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

        self.mock_token_service = MagicMock()
        self.mock_token_service.verify_token = MagicMock(return_value=self.mock_payload)
        self.mock_token_service.create_access_token = MagicMock(return_value="new_access_token")
        self.mock_token_service.create_refresh_token = MagicMock(return_value="new_refresh_token")

        self.use_case = RefreshTokenUseCase(
            user_repository=self.mock_user_repo,
            token_service=self.mock_token_service,
        )

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        """Test successful token refresh."""
        dto = RefreshTokenDTO(refresh_token="valid_refresh_token")

        result = await self.use_case.execute(dto)

        assert result is not None
        assert result.access_token == "new_access_token"
        assert result.refresh_token == "new_refresh_token"
        self.mock_token_service.verify_token.assert_called_once_with("valid_refresh_token")

    @pytest.mark.asyncio
    async def test_refresh_token_user_not_found(self):
        """Test refresh when user no longer exists."""
        self.mock_user_repo.get_by_id.return_value = None

        dto = RefreshTokenDTO(refresh_token="valid_token")

        with pytest.raises(EntityNotFoundError):
            await self.use_case.execute(dto)
