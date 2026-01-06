"""Unit tests for user entity."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.core.entities.user import User, UserRole
from src.core.value_objects import Email, PhoneNumber


class TestUserEntity:
    """Tests for the User entity."""

    def test_create_user_with_valid_data(self):
        """Test creating a user with valid data."""
        user = User(
            id=uuid4(),
            email=Email("test@example.com"),
            password_hash="hashed_password",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
        )
        
        assert user.email.value == "test@example.com"
        assert user.first_name == "John"
        assert user.last_name == "Doe"
        assert user.full_name == "John Doe"
        assert user.role == UserRole.CUSTOMER
        assert user.is_active is True
        assert user.is_verified is False

    def test_user_full_name(self):
        """Test user full name property."""
        user = User(
            id=uuid4(),
            email=Email("test@example.com"),
            password_hash="hashed_password",
            first_name="Jane",
            last_name="Smith",
            role=UserRole.CUSTOMER,
        )
        
        assert user.full_name == "Jane Smith"

    def test_activate_user(self):
        """Test activating a user."""
        user = User(
            id=uuid4(),
            email=Email("test@example.com"),
            password_hash="hashed_password",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            is_active=False,
        )
        
        assert user.is_active is False
        user.activate()
        assert user.is_active is True

    def test_deactivate_user(self):
        """Test deactivating a user."""
        user = User(
            id=uuid4(),
            email=Email("test@example.com"),
            password_hash="hashed_password",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            is_active=True,
        )
        
        assert user.is_active is True
        user.deactivate()
        assert user.is_active is False

    def test_verify_user(self):
        """Test verifying a user."""
        user = User(
            id=uuid4(),
            email=Email("test@example.com"),
            password_hash="hashed_password",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            is_verified=False,
        )
        
        assert user.is_verified is False
        user.verify()
        assert user.is_verified is True

    def test_user_with_phone(self):
        """Test user with phone number."""
        user = User(
            id=uuid4(),
            email=Email("test@example.com"),
            password_hash="hashed_password",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            phone=PhoneNumber("+1234567890"),
        )
        
        assert user.phone is not None
        assert user.phone.value == "+1234567890"

    def test_user_roles(self):
        """Test different user roles."""
        roles = [
            UserRole.CUSTOMER,
            UserRole.STORE_OWNER,
            UserRole.SUPER_ADMIN,
        ]
        
        for role in roles:
            user = User(
                id=uuid4(),
                email=Email("test@example.com"),
                password_hash="hashed_password",
                first_name="John",
                last_name="Doe",
                role=role,
            )
            assert user.role == role
