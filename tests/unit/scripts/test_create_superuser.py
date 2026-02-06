"""Unit tests for create_superuser script."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import UUID

import pytest

from src.core.entities.user import UserRole, UserStatus

# Load the create_superuser module directly
spec = importlib.util.spec_from_file_location(
    "create_superuser",
    Path(__file__).parent.parent.parent.parent / "scripts" / "create_superuser.py",
)
create_superuser_module = importlib.util.module_from_spec(spec)
# Register the module in sys.modules so that patching works
sys.modules["create_superuser"] = create_superuser_module
spec.loader.exec_module(create_superuser_module)
create_superuser = create_superuser_module.create_superuser
promote_to_superuser = create_superuser_module.promote_to_superuser
validate_email = create_superuser_module.validate_email
validate_password = create_superuser_module.validate_password


def create_async_session_mock(existing_user=None):
    """Helper to create a properly mocked async session."""
    mock_session = MagicMock()

    # Mock the execute result
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = Mock(return_value=existing_user)

    # Make execute async
    async def mock_execute(*args, **kwargs):
        return mock_execute_result

    mock_session.execute = mock_execute

    # Track commit calls
    commit_called = []

    async def mock_commit():
        commit_called.append(True)

    mock_session.commit = mock_commit
    mock_session.commit_called = commit_called

    # add is sync
    mock_session.add = Mock()

    # Create context manager
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    return mock_session, mock_cm


class TestValidation:
    """Tests for validation functions."""

    def test_validate_email_valid(self):
        """Test valid email addresses."""
        assert validate_email("admin@example.com") is True
        assert validate_email("user.name@domain.co") is True
        assert validate_email("test+tag@subdomain.example.org") is True

    def test_validate_email_invalid(self):
        """Test invalid email addresses."""
        assert validate_email("") is False
        assert validate_email("notanemail") is False
        assert validate_email("missing@domain") is False
        assert validate_email("@nodomain.com") is False

    def test_validate_password_valid(self):
        """Test valid passwords."""
        valid, _ = validate_password("SecurePass123")
        assert valid is True
        valid, _ = validate_password("MyP@ssw0rd!")
        assert valid is True

    def test_validate_password_too_short(self):
        """Test password too short."""
        valid, msg = validate_password("Short1")
        assert valid is False
        assert "8 characters" in msg

    def test_validate_password_no_letter(self):
        """Test password without letters."""
        valid, msg = validate_password("12345678")
        assert valid is False
        assert "letter" in msg

    def test_validate_password_no_number(self):
        """Test password without numbers."""
        valid, msg = validate_password("NoNumbersHere")
        assert valid is False
        assert "number" in msg


class TestCreateSuperuser:
    """Tests for the create_superuser function."""

    @pytest.mark.asyncio
    async def test_create_superuser_success(self):
        """Test successful superuser creation."""
        email = "admin@example.com"
        password = "SecurePassword123!"
        first_name = "Super"
        last_name = "Admin"

        # Create mocked session (no existing user)
        mock_session, mock_cm = create_async_session_mock(existing_user=None)

        # Mock password service
        with patch("create_superuser.password_service.hash_password") as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"

            # Mock AsyncSessionLocal context manager
            with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
                with patch("builtins.print") as mock_print:
                    result = await create_superuser(
                        email, password, first_name, last_name
                    )

                    # Verify success
                    assert result is True
                    # Verify success message was printed
                    mock_print.assert_any_call("\n✅ Superuser created successfully!")

        # Verify session operations were called
        assert mock_session.add.called
        assert len(mock_session.commit_called) > 0

    @pytest.mark.asyncio
    async def test_create_superuser_prevents_duplicates(self):
        """Test that duplicate superuser creation is prevented."""
        email = "admin@example.com"
        password = "SecurePassword123!"

        # Create existing user
        existing_user = MagicMock()
        existing_user.email = email

        # Create mocked session with existing user
        mock_session, mock_cm = create_async_session_mock(existing_user=existing_user)

        with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
            with patch("builtins.print") as mock_print:
                result = await create_superuser(email, password)

                # Verify failure
                assert result is False
                # Verify error message was printed
                mock_print.assert_any_call(
                    f"\n❌ User with email '{email}' already exists!"
                )

        # Verify no user was added
        assert not mock_session.add.called
        assert len(mock_session.commit_called) == 0

    @pytest.mark.asyncio
    async def test_password_is_hashed(self):
        """Test that passwords are properly hashed."""
        email = "admin@example.com"
        password = "PlainTextPassword123!"
        hashed_password = "$2b$12$LzP.VDm5wZJKl6bJj0/fwON4A2JfH4jO4zJ3pZ3h2aQ3tJ5K6m7Km"

        # Create mocked session
        mock_session, mock_cm = create_async_session_mock(existing_user=None)

        # Track the user model that was added
        added_user = None

        def capture_add(user):
            nonlocal added_user
            added_user = user

        mock_session.add = Mock(side_effect=capture_add)

        # Mock password service
        with patch("create_superuser.password_service.hash_password") as mock_hash:
            mock_hash.return_value = hashed_password

            with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
                with patch("builtins.print"):
                    await create_superuser(email, password)

        # Verify password was hashed
        mock_hash.assert_called_once_with(password)
        # Verify the hashed password was used
        assert added_user is not None
        assert added_user.hashed_password == hashed_password
        assert added_user.hashed_password != password
        assert added_user.hashed_password.startswith("$2b$")

    @pytest.mark.asyncio
    async def test_correct_role_assignment(self):
        """Test that superuser is assigned the SUPER_ADMIN role."""
        email = "admin@example.com"
        password = "SecurePassword123!"

        # Create mocked session
        mock_session, mock_cm = create_async_session_mock(existing_user=None)

        # Track the user model that was added
        added_user = None

        def capture_add(user):
            nonlocal added_user
            added_user = user

        mock_session.add = Mock(side_effect=capture_add)

        # Mock password service
        with patch("create_superuser.password_service.hash_password") as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"

            with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
                with patch("builtins.print"):
                    await create_superuser(email, password)

        # Verify role is SUPER_ADMIN
        assert added_user is not None
        assert added_user.role == UserRole.SUPER_ADMIN
        # Also verify it's not any other role
        assert added_user.role != UserRole.CUSTOMER
        assert added_user.role != UserRole.STORE_OWNER
        assert added_user.role != UserRole.STORE_ADMIN
        assert added_user.role != UserRole.STORE_STAFF

    @pytest.mark.asyncio
    async def test_superuser_is_active(self):
        """Test that superuser is created with ACTIVE status."""
        email = "admin@example.com"
        password = "SecurePassword123!"

        # Create mocked session
        mock_session, mock_cm = create_async_session_mock(existing_user=None)

        # Track the user model that was added
        added_user = None

        def capture_add(user):
            nonlocal added_user
            added_user = user

        mock_session.add = Mock(side_effect=capture_add)

        # Mock password service
        with patch("create_superuser.password_service.hash_password") as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"

            with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
                with patch("builtins.print"):
                    await create_superuser(email, password)

        # Verify status is ACTIVE
        assert added_user is not None
        assert added_user.status == UserStatus.ACTIVE
        # Verify it's not any other status
        assert added_user.status != UserStatus.INACTIVE
        assert added_user.status != UserStatus.SUSPENDED
        assert added_user.status != UserStatus.PENDING_VERIFICATION

    @pytest.mark.asyncio
    async def test_superuser_email_is_lowercased(self):
        """Test that superuser email is stored in lowercase."""
        email = "Admin@Example.COM"
        password = "SecurePassword123!"

        # Create mocked session
        mock_session, mock_cm = create_async_session_mock(existing_user=None)

        # Track the user model that was added
        added_user = None

        def capture_add(user):
            nonlocal added_user
            added_user = user

        mock_session.add = Mock(side_effect=capture_add)

        # Mock password service
        with patch("create_superuser.password_service.hash_password") as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"

            with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
                with patch("builtins.print"):
                    await create_superuser(email, password)

        # Verify email is lowercase
        assert added_user is not None
        assert added_user.email == email.lower()

    @pytest.mark.asyncio
    async def test_superuser_has_valid_uuid(self):
        """Test that superuser is created with a valid UUID."""
        email = "admin@example.com"
        password = "SecurePassword123!"

        # Create mocked session
        mock_session, mock_cm = create_async_session_mock(existing_user=None)

        # Track the user model that was added
        added_user = None

        def capture_add(user):
            nonlocal added_user
            added_user = user

        mock_session.add = Mock(side_effect=capture_add)

        # Mock password service
        with patch("create_superuser.password_service.hash_password") as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"

            with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
                with patch("builtins.print"):
                    await create_superuser(email, password)

        # Verify user has a valid UUID
        assert added_user is not None
        assert added_user.id is not None
        assert isinstance(added_user.id, UUID)
        # Verify it's a valid UUID by checking its string representation
        assert len(str(added_user.id)) == 36  # Standard UUID string length
        assert str(added_user.id).count("-") == 4  # UUIDs have 4 hyphens

    @pytest.mark.asyncio
    async def test_superuser_name_fields(self):
        """Test that superuser first_name and last_name are set correctly."""
        email = "admin@example.com"
        password = "SecurePassword123!"
        first_name = "John"
        last_name = "Doe"

        # Create mocked session
        mock_session, mock_cm = create_async_session_mock(existing_user=None)

        # Track the user model that was added
        added_user = None

        def capture_add(user):
            nonlocal added_user
            added_user = user

        mock_session.add = Mock(side_effect=capture_add)

        # Mock password service
        with patch("create_superuser.password_service.hash_password") as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"

            with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
                with patch("builtins.print"):
                    await create_superuser(email, password, first_name, last_name)

        # Verify name fields
        assert added_user is not None
        assert added_user.first_name == first_name
        assert added_user.last_name == last_name

    @pytest.mark.asyncio
    async def test_superuser_default_name_fields(self):
        """Test that superuser uses default name fields when not provided."""
        email = "admin@example.com"
        password = "SecurePassword123!"

        # Create mocked session
        mock_session, mock_cm = create_async_session_mock(existing_user=None)

        # Track the user model that was added
        added_user = None

        def capture_add(user):
            nonlocal added_user
            added_user = user

        mock_session.add = Mock(side_effect=capture_add)

        # Mock password service
        with patch("create_superuser.password_service.hash_password") as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"

            with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
                with patch("builtins.print"):
                    await create_superuser(email, password)

        # Verify default name fields
        assert added_user is not None
        assert added_user.first_name == "Super"
        assert added_user.last_name == "Admin"


class TestPromoteToSuperuser:
    """Tests for the promote_to_superuser function."""

    @pytest.mark.asyncio
    async def test_promote_existing_user(self):
        """Test promoting an existing user to superadmin."""
        email = "user@example.com"

        # Create existing user with non-admin role
        existing_user = MagicMock()
        existing_user.email = email
        existing_user.role = UserRole.STORE_OWNER

        # Create mocked session with existing user
        _, mock_cm = create_async_session_mock(existing_user=existing_user)

        with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
            with patch("builtins.print") as mock_print:
                result = await promote_to_superuser(email)

                # Verify success
                assert result is True
                mock_print.assert_any_call("\n✅ User promoted to superadmin!")

        # Verify role was updated
        assert existing_user.role == UserRole.SUPER_ADMIN
        assert existing_user.status == UserStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_promote_nonexistent_user(self):
        """Test promoting a non-existent user fails."""
        email = "nonexistent@example.com"

        # Create mocked session with no user
        _, mock_cm = create_async_session_mock(existing_user=None)

        with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
            with patch("builtins.print") as mock_print:
                result = await promote_to_superuser(email)

                # Verify failure
                assert result is False
                mock_print.assert_any_call(f"\n❌ User with email '{email}' not found!")

    @pytest.mark.asyncio
    async def test_promote_already_superadmin(self):
        """Test promoting a user who is already superadmin."""
        email = "admin@example.com"

        # Create existing superadmin
        existing_user = MagicMock()
        existing_user.email = email
        existing_user.role = UserRole.SUPER_ADMIN

        # Create mocked session with existing user
        _, mock_cm = create_async_session_mock(existing_user=existing_user)

        with patch("create_superuser.AsyncSessionLocal", return_value=mock_cm):
            with patch("builtins.print") as mock_print:
                result = await promote_to_superuser(email)

                # Should still return True (idempotent)
                assert result is True
                mock_print.assert_any_call(
                    f"\n⚠️  User '{email}' is already a superadmin!"
                )
