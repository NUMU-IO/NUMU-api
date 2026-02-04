"""Unit tests for admin authentication backend."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse

from src.api.admin.auth import AdminAuth
from src.core.entities.user import UserRole
from src.infrastructure.database.models import UserModel


@pytest.fixture
def admin_auth():
    """Create AdminAuth instance for testing."""
    return AdminAuth(secret_key="test-secret-key")


@pytest.fixture
def mock_request():
    """Create a mock request object."""
    request = MagicMock(spec=Request)
    request.session = {}
    return request


@pytest.fixture
def mock_super_admin_user():
    """Create a mock super admin user."""
    user = MagicMock(spec=UserModel)
    user.id = uuid4()
    user.email = "admin@example.com"
    user.hashed_password = (
        "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5L7BW/z9QyxKm"  # "password123"
    )
    user.role = UserRole.SUPER_ADMIN
    return user


@pytest.fixture
def mock_regular_user():
    """Create a mock regular user (non-admin)."""
    user = MagicMock(spec=UserModel)
    user.id = uuid4()
    user.email = "user@example.com"
    user.hashed_password = (
        "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5L7BW/z9QyxKm"  # "password123"
    )
    user.role = UserRole.CUSTOMER
    return user


class TestAdminAuthLogin:
    """Tests for AdminAuth.login() method."""

    @pytest.mark.asyncio
    async def test_login_success_with_super_admin(
        self, admin_auth, mock_request, mock_super_admin_user
    ):
        """Test successful login with SUPER_ADMIN role."""
        # Setup form data
        mock_request.form = AsyncMock(
            return_value=FormData([
                ("username", "admin@example.com"),
                ("password", "password123"),
            ])
        )

        # Mock database session and query
        with (
            patch("src.api.admin.auth.AsyncSessionLocal") as mock_session_local,
            patch("src.api.admin.auth.password_service") as mock_password_service,
        ):
            # Setup mock session context manager
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None

            # Setup mock execute result
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_super_admin_user
            mock_session.execute = AsyncMock(return_value=mock_result)

            # Setup password verification
            mock_password_service.verify_password.return_value = True

            # Execute login
            result = await admin_auth.login(mock_request)

            # Assertions
            assert result is True
            assert mock_request.session["admin_user_id"] == str(
                mock_super_admin_user.id
            )
            assert mock_request.session["admin_email"] == mock_super_admin_user.email
            mock_password_service.verify_password.assert_called_once_with(
                "password123", mock_super_admin_user.hashed_password
            )

    @pytest.mark.asyncio
    async def test_login_fails_with_missing_credentials(self, admin_auth, mock_request):
        """Test login fails when credentials are missing."""
        # Test with empty form
        mock_request.form = AsyncMock(return_value=FormData([]))
        result = await admin_auth.login(mock_request)
        assert result is False

        # Test with missing password
        mock_request.form = AsyncMock(
            return_value=FormData([("username", "admin@example.com")])
        )
        result = await admin_auth.login(mock_request)
        assert result is False

        # Test with missing username
        mock_request.form = AsyncMock(
            return_value=FormData([("password", "password123")])
        )
        result = await admin_auth.login(mock_request)
        assert result is False

    @pytest.mark.asyncio
    async def test_login_fails_with_incorrect_password(
        self, admin_auth, mock_request, mock_super_admin_user
    ):
        """Test login fails with incorrect password."""
        mock_request.form = AsyncMock(
            return_value=FormData([
                ("username", "admin@example.com"),
                ("password", "wrongpassword"),
            ])
        )

        with (
            patch("src.api.admin.auth.AsyncSessionLocal") as mock_session_local,
            patch("src.api.admin.auth.password_service") as mock_password_service,
        ):
            # Setup mock session
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None

            # Setup mock execute result
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_super_admin_user
            mock_session.execute = AsyncMock(return_value=mock_result)

            # Setup password verification to fail
            mock_password_service.verify_password.return_value = False

            # Execute login
            result = await admin_auth.login(mock_request)

            # Assertions
            assert result is False
            assert "admin_user_id" not in mock_request.session
            mock_password_service.verify_password.assert_called_once_with(
                "wrongpassword", mock_super_admin_user.hashed_password
            )

    @pytest.mark.asyncio
    async def test_login_fails_with_non_existent_user(self, admin_auth, mock_request):
        """Test login fails when user doesn't exist."""
        mock_request.form = AsyncMock(
            return_value=FormData([
                ("username", "nonexistent@example.com"),
                ("password", "password123"),
            ])
        )

        with patch("src.api.admin.auth.AsyncSessionLocal") as mock_session_local:
            # Setup mock session
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None

            # Setup mock execute result - no user found
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute = AsyncMock(return_value=mock_result)

            # Execute login
            result = await admin_auth.login(mock_request)

            # Assertions
            assert result is False
            assert "admin_user_id" not in mock_request.session

    @pytest.mark.asyncio
    async def test_login_fails_with_non_super_admin_role(
        self, admin_auth, mock_request, mock_regular_user
    ):
        """Test login fails with non-SUPER_ADMIN roles."""
        mock_request.form = AsyncMock(
            return_value=FormData([
                ("username", "user@example.com"),
                ("password", "password123"),
            ])
        )

        with (
            patch("src.api.admin.auth.AsyncSessionLocal") as mock_session_local,
            patch("src.api.admin.auth.password_service") as mock_password_service,
        ):
            # Setup mock session
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None

            # Setup mock execute result
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_regular_user
            mock_session.execute = AsyncMock(return_value=mock_result)

            # Setup password verification to succeed
            mock_password_service.verify_password.return_value = True

            # Execute login
            result = await admin_auth.login(mock_request)

            # Assertions
            assert result is False
            assert "admin_user_id" not in mock_request.session

    @pytest.mark.asyncio
    async def test_login_fails_with_customer_role(
        self, admin_auth, mock_request, mock_regular_user
    ):
        """Test login explicitly fails with CUSTOMER role."""
        mock_regular_user.role = UserRole.CUSTOMER
        mock_request.form = AsyncMock(
            return_value=FormData([
                ("username", "user@example.com"),
                ("password", "password123"),
            ])
        )

        with (
            patch("src.api.admin.auth.AsyncSessionLocal") as mock_session_local,
            patch("src.api.admin.auth.password_service") as mock_password_service,
        ):
            # Setup mock session
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None

            # Setup mock execute result
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_regular_user
            mock_session.execute = AsyncMock(return_value=mock_result)

            # Setup password verification to succeed
            mock_password_service.verify_password.return_value = True

            # Execute login
            result = await admin_auth.login(mock_request)

            # Assertions
            assert result is False
            assert "admin_user_id" not in mock_request.session

    @pytest.mark.asyncio
    async def test_login_fails_with_store_owner_role(
        self, admin_auth, mock_request, mock_regular_user
    ):
        """Test login explicitly fails with STORE_OWNER role."""
        mock_regular_user.role = UserRole.STORE_OWNER
        mock_request.form = AsyncMock(
            return_value=FormData([
                ("username", "owner@example.com"),
                ("password", "password123"),
            ])
        )

        with (
            patch("src.api.admin.auth.AsyncSessionLocal") as mock_session_local,
            patch("src.api.admin.auth.password_service") as mock_password_service,
        ):
            # Setup mock session
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None

            # Setup mock execute result
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_regular_user
            mock_session.execute = AsyncMock(return_value=mock_result)

            # Setup password verification to succeed
            mock_password_service.verify_password.return_value = True

            # Execute login
            result = await admin_auth.login(mock_request)

            # Assertions
            assert result is False
            assert "admin_user_id" not in mock_request.session

    @pytest.mark.asyncio
    async def test_login_handles_database_exception(self, admin_auth, mock_request):
        """Test login handles database exceptions gracefully."""
        mock_request.form = AsyncMock(
            return_value=FormData([
                ("username", "admin@example.com"),
                ("password", "password123"),
            ])
        )

        with patch("src.api.admin.auth.AsyncSessionLocal") as mock_session_local:
            # Setup mock session to raise exception
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None
            mock_session.execute = AsyncMock(side_effect=Exception("Database error"))

            # Execute login
            result = await admin_auth.login(mock_request)

            # Assertions
            assert result is False
            assert "admin_user_id" not in mock_request.session


class TestAdminAuthLogout:
    """Tests for AdminAuth.logout() method."""

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, admin_auth, mock_request):
        """Test logout clears the session."""
        # Setup session with data
        mock_session = MagicMock()
        mock_session.clear = MagicMock()
        mock_request.session = mock_session

        # Execute logout
        result = await admin_auth.logout(mock_request)

        # Assertions
        assert result is True
        mock_session.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_logout_with_empty_session(self, admin_auth, mock_request):
        """Test logout works with empty session."""
        mock_session = MagicMock()
        mock_session.clear = MagicMock()
        mock_request.session = mock_session

        # Execute logout
        result = await admin_auth.logout(mock_request)

        # Assertions
        assert result is True
        mock_session.clear.assert_called_once()


class TestAdminAuthAuthenticate:
    """Tests for AdminAuth.authenticate() method."""

    @pytest.mark.asyncio
    async def test_authenticate_success_with_valid_session(
        self, admin_auth, mock_request
    ):
        """Test authenticate succeeds with valid session."""
        # Setup session with admin user ID
        mock_request.session = {
            "admin_user_id": str(uuid4()),
            "admin_email": "admin@example.com",
        }

        # Execute authenticate
        result = await admin_auth.authenticate(mock_request)

        # Assertions
        assert result is True

    @pytest.mark.asyncio
    async def test_authenticate_redirects_without_session(
        self, admin_auth, mock_request
    ):
        """Test authenticate redirects to login without session."""
        # Setup empty session
        mock_request.session = {}
        mock_request.url_for = MagicMock(return_value="/admin/login")

        # Execute authenticate
        result = await admin_auth.authenticate(mock_request)

        # Assertions
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 302
        mock_request.url_for.assert_called_once_with("admin:login")

    @pytest.mark.asyncio
    async def test_authenticate_redirects_with_none_session_value(
        self, admin_auth, mock_request
    ):
        """Test authenticate redirects when admin_user_id is None."""
        # Setup session with None value
        mock_request.session = {"admin_user_id": None}
        mock_request.url_for = MagicMock(return_value="/admin/login")

        # Execute authenticate
        result = await admin_auth.authenticate(mock_request)

        # Assertions
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 302

    @pytest.mark.asyncio
    async def test_authenticate_redirects_with_missing_admin_user_id(
        self, admin_auth, mock_request
    ):
        """Test authenticate redirects when admin_user_id key is missing."""
        # Setup session without admin_user_id
        mock_request.session = {"other_key": "other_value"}
        mock_request.url_for = MagicMock(return_value="/admin/login")

        # Execute authenticate
        result = await admin_auth.authenticate(mock_request)

        # Assertions
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 302


class TestAdminAuthSessionPersistence:
    """Tests for session persistence across authentication methods."""

    @pytest.mark.asyncio
    async def test_session_persists_after_login(
        self, admin_auth, mock_request, mock_super_admin_user
    ):
        """Test that session data persists after successful login."""
        mock_request.form = AsyncMock(
            return_value=FormData([
                ("username", "admin@example.com"),
                ("password", "password123"),
            ])
        )

        with (
            patch("src.api.admin.auth.AsyncSessionLocal") as mock_session_local,
            patch("src.api.admin.auth.password_service") as mock_password_service,
        ):
            # Setup mock session
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None

            # Setup mock execute result
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_super_admin_user
            mock_session.execute = AsyncMock(return_value=mock_result)

            # Setup password verification
            mock_password_service.verify_password.return_value = True

            # Execute login
            login_result = await admin_auth.login(mock_request)

            # Verify login succeeded
            assert login_result is True

            # Verify session was updated
            assert "admin_user_id" in mock_request.session
            assert "admin_email" in mock_request.session

            # Now test authenticate with the same session
            auth_result = await admin_auth.authenticate(mock_request)

            # Verify authentication succeeds with persisted session
            assert auth_result is True

    @pytest.mark.asyncio
    async def test_session_cleared_after_logout(
        self, admin_auth, mock_request, mock_super_admin_user
    ):
        """Test that session is cleared after logout."""
        # Setup initial session
        mock_request.session = {
            "admin_user_id": str(mock_super_admin_user.id),
            "admin_email": mock_super_admin_user.email,
        }

        # Verify authenticate works before logout
        auth_before = await admin_auth.authenticate(mock_request)
        assert auth_before is True

        # Mock the session.clear method for verification
        mock_session = MagicMock()
        mock_session.clear = MagicMock()
        mock_request.session = mock_session

        # Execute logout
        logout_result = await admin_auth.logout(mock_request)
        assert logout_result is True

        # Verify session.clear() was called
        mock_session.clear.assert_called_once()

        # Simulate cleared session
        mock_request.session = {}
        mock_request.url_for = MagicMock(return_value="/admin/login")

        # Verify authenticate redirects after logout
        auth_after = await admin_auth.authenticate(mock_request)
        assert isinstance(auth_after, RedirectResponse)
        assert auth_after.status_code == 302

    @pytest.mark.asyncio
    async def test_session_data_integrity(
        self, admin_auth, mock_request, mock_super_admin_user
    ):
        """Test that session data maintains integrity."""
        expected_user_id = str(mock_super_admin_user.id)
        expected_email = mock_super_admin_user.email

        mock_request.form = AsyncMock(
            return_value=FormData([
                ("username", expected_email),
                ("password", "password123"),
            ])
        )

        with (
            patch("src.api.admin.auth.AsyncSessionLocal") as mock_session_local,
            patch("src.api.admin.auth.password_service") as mock_password_service,
        ):
            # Setup mock session
            mock_session = AsyncMock()
            mock_session_local.return_value.__aenter__.return_value = mock_session
            mock_session_local.return_value.__aexit__.return_value = None

            # Setup mock execute result
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_super_admin_user
            mock_session.execute = AsyncMock(return_value=mock_result)

            # Setup password verification
            mock_password_service.verify_password.return_value = True

            # Execute login
            await admin_auth.login(mock_request)

            # Verify exact session data
            assert mock_request.session["admin_user_id"] == expected_user_id
            assert mock_request.session["admin_email"] == expected_email
            assert len(mock_request.session) == 2  # Only these two keys should exist
