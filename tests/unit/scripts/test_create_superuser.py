"""Unit tests for create_superuser script."""

import sys
from pathlib import Path
import importlib.util

import pytest
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call
from sqlalchemy import select

from src.infrastructure.database.models.public.user import UserModel
from src.core.entities.user import UserRole, UserStatus

# Load the create_superuser module directly
spec = importlib.util.spec_from_file_location(
    "create_superuser",
    Path(__file__).parent.parent.parent.parent / "scripts" / "create_superuser.py"
)
create_superuser_module = importlib.util.module_from_spec(spec)
# Register the module in sys.modules so that patching works
sys.modules['create_superuser'] = create_superuser_module
spec.loader.exec_module(create_superuser_module)
create_superuser = create_superuser_module.create_superuser


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


class TestCreateSuperuser:
    """Tests for the create_superuser function."""

    @pytest.mark.asyncio
    async def test_create_superuser_success(self):
        """Test successful superuser creation."""
        name = "admin"
        password = "SecurePassword123!"
        
        # Create mocked session (no existing user)
        mock_session, mock_cm = create_async_session_mock(existing_user=None)
        
        # Mock password service
        with patch('create_superuser.password_service.hash_password') as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"
            
            # Mock AsyncSessionLocal context manager
            with patch('create_superuser.AsyncSessionLocal', return_value=mock_cm):
                with patch('builtins.print') as mock_print:
                    await create_superuser(name, password)
                    
                    # Verify success message was printed
                    mock_print.assert_any_call(f"✅ Superuser '{name}' created!")
        
        # Verify session operations were called
        assert mock_session.add.called
        assert len(mock_session.commit_called) > 0

    @pytest.mark.asyncio
    async def test_create_superuser_prevents_duplicates(self):
        """Test that duplicate superuser creation is prevented."""
        name = "admin"
        password = "SecurePassword123!"
        
        # Create existing user
        existing_user = MagicMock()
        existing_user.email = f"{name}@admin.local"
        
        # Create mocked session with existing user
        mock_session, mock_cm = create_async_session_mock(existing_user=existing_user)
        
        with patch('create_superuser.AsyncSessionLocal', return_value=mock_cm):
            with patch('builtins.print') as mock_print:
                await create_superuser(name, password)
                
                # Verify error message was printed
                mock_print.assert_called_with(f"❌ User '{name}' already exists!")
        
        # Verify no user was added
        assert not mock_session.add.called
        assert len(mock_session.commit_called) == 0

    @pytest.mark.asyncio
    async def test_password_is_hashed(self):
        """Test that passwords are properly hashed."""
        name = "admin"
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
        with patch('create_superuser.password_service.hash_password') as mock_hash:
            mock_hash.return_value = hashed_password
            
            with patch('create_superuser.AsyncSessionLocal', return_value=mock_cm):
                with patch('builtins.print'):
                    await create_superuser(name, password)
        
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
        name = "admin"
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
        with patch('create_superuser.password_service.hash_password') as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"
            
            with patch('create_superuser.AsyncSessionLocal', return_value=mock_cm):
                with patch('builtins.print'):
                    await create_superuser(name, password)
        
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
        name = "admin"
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
        with patch('create_superuser.password_service.hash_password') as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"
            
            with patch('create_superuser.AsyncSessionLocal', return_value=mock_cm):
                with patch('builtins.print'):
                    await create_superuser(name, password)
        
        # Verify status is ACTIVE
        assert added_user is not None
        assert added_user.status == UserStatus.ACTIVE
        # Verify it's not any other status
        assert added_user.status != UserStatus.INACTIVE
        assert added_user.status != UserStatus.SUSPENDED
        assert added_user.status != UserStatus.PENDING_VERIFICATION

    @pytest.mark.asyncio
    async def test_superuser_email_format(self):
        """Test that superuser email follows the correct format."""
        name = "testadmin"
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
        with patch('create_superuser.password_service.hash_password') as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"
            
            with patch('create_superuser.AsyncSessionLocal', return_value=mock_cm):
                with patch('builtins.print'):
                    await create_superuser(name, password)
        
        # Verify email format
        assert added_user is not None
        expected_email = f"{name}@admin.local"
        assert added_user.email == expected_email
        assert "@admin.local" in added_user.email

    @pytest.mark.asyncio
    async def test_superuser_has_valid_uuid(self):
        """Test that superuser is created with a valid UUID."""
        name = "admin"
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
        with patch('create_superuser.password_service.hash_password') as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"
            
            with patch('create_superuser.AsyncSessionLocal', return_value=mock_cm):
                with patch('builtins.print'):
                    await create_superuser(name, password)
        
        # Verify user has a valid UUID
        assert added_user is not None
        assert added_user.id is not None
        assert isinstance(added_user.id, UUID)
        # Verify it's a valid UUID by checking its string representation
        assert len(str(added_user.id)) == 36  # Standard UUID string length
        assert str(added_user.id).count('-') == 4  # UUIDs have 4 hyphens

    @pytest.mark.asyncio
    async def test_superuser_name_fields(self):
        """Test that superuser first_name and last_name are set correctly."""
        name = "customadmin"
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
        with patch('create_superuser.password_service.hash_password') as mock_hash:
            mock_hash.return_value = "$2b$12$hashedpassword"
            
            with patch('create_superuser.AsyncSessionLocal', return_value=mock_cm):
                with patch('builtins.print'):
                    await create_superuser(name, password)
        
        # Verify name fields
        assert added_user is not None
        assert added_user.first_name == name
        assert added_user.last_name == "Admin"
