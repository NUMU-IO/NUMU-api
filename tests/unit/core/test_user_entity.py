"""Unit tests for User entity."""

from datetime import datetime
from uuid import uuid4

import pytest

from src.core.entities.user import User, UserRole, UserStatus
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber


class TestUserEntity:
    """Tests for the User entity."""

    def test_create_user_with_valid_data(self):
        """Test creating a user with valid data."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            status=UserStatus.ACTIVE,
        )

        assert user.email.value == "test@example.com"
        assert user.first_name == "John"
        assert user.last_name == "Doe"
        assert user.full_name == "John Doe"
        assert user.role == UserRole.CUSTOMER
        assert user.is_active is True
        assert user.is_verified is False  # No email_verified_at set

    def test_user_full_name(self):
        """Test user full name property."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Jane",
            last_name="Smith",
            role=UserRole.CUSTOMER,
        )

        assert user.full_name == "Jane Smith"

    def test_activate_user(self):
        """Test activating a user."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            status=UserStatus.INACTIVE,
        )

        assert user.is_active is False
        user.activate()
        assert user.is_active is True
        assert user.status == UserStatus.ACTIVE

    def test_deactivate_user(self):
        """Test deactivating a user."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            status=UserStatus.ACTIVE,
        )

        assert user.is_active is True
        user.deactivate()
        assert user.is_active is False
        assert user.status == UserStatus.INACTIVE

    def test_verify_user_email(self):
        """Test verifying a user's email."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            status=UserStatus.PENDING_VERIFICATION,
        )

        assert user.is_verified is False
        user.verify_email()
        assert user.is_verified is True
        assert user.email_verified_at is not None
        assert user.status == UserStatus.ACTIVE

    def test_suspend_user(self):
        """Test suspending a user."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            status=UserStatus.ACTIVE,
        )

        user.suspend()
        assert user.status == UserStatus.SUSPENDED

    def test_user_with_phone(self):
        """Test user with phone number."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            phone=PhoneNumber(value="+201234567890", country_code="EG"),
        )

        assert user.phone is not None
        assert user.phone.value == "+201234567890"

    def test_user_roles(self):
        """Test different user roles."""
        roles = [
            UserRole.CUSTOMER,
            UserRole.STORE_OWNER,
            UserRole.STORE_ADMIN,
            UserRole.STORE_STAFF,
            UserRole.SUPER_ADMIN,
        ]

        for role in roles:
            user = User(
                id=uuid4(),
                email=Email(value="test@example.com"),
                hashed_password="$2b$12$hashedpassword",
                first_name="John",
                last_name="Doe",
                role=role,
            )
            assert user.role == role

    def test_user_is_store_owner(self):
        """Test is_store_owner property."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.STORE_OWNER,
        )

        assert user.is_store_owner is True

        customer = User(
            id=uuid4(),
            email=Email(value="customer@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Jane",
            last_name="Doe",
            role=UserRole.CUSTOMER,
        )

        assert customer.is_store_owner is False

    def test_user_is_admin(self):
        """Test is_admin property."""
        super_admin = User(
            id=uuid4(),
            email=Email(value="admin@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Admin",
            last_name="User",
            role=UserRole.SUPER_ADMIN,
        )

        store_admin = User(
            id=uuid4(),
            email=Email(value="storeadmin@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Store",
            last_name="Admin",
            role=UserRole.STORE_ADMIN,
        )

        customer = User(
            id=uuid4(),
            email=Email(value="customer@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Jane",
            last_name="Customer",
            role=UserRole.CUSTOMER,
        )

        assert super_admin.is_admin is True
        assert store_admin.is_admin is True
        assert customer.is_admin is False

    def test_user_is_super_admin(self):
        """Test is_super_admin property."""
        super_admin = User(
            id=uuid4(),
            email=Email(value="admin@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Admin",
            last_name="User",
            role=UserRole.SUPER_ADMIN,
        )

        store_owner = User(
            id=uuid4(),
            email=Email(value="owner@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Store",
            last_name="Owner",
            role=UserRole.STORE_OWNER,
        )

        assert super_admin.is_super_admin is True
        assert store_owner.is_super_admin is False

    def test_user_can_manage_store(self):
        """Test can_manage_store method."""
        store_owner = User(
            id=uuid4(),
            email=Email(value="owner@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Store",
            last_name="Owner",
            role=UserRole.STORE_OWNER,
        )

        store_admin = User(
            id=uuid4(),
            email=Email(value="admin@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Store",
            last_name="Admin",
            role=UserRole.STORE_ADMIN,
        )

        customer = User(
            id=uuid4(),
            email=Email(value="customer@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Jane",
            last_name="Customer",
            role=UserRole.CUSTOMER,
        )

        assert store_owner.can_manage_store() is True
        assert store_admin.can_manage_store() is True
        assert customer.can_manage_store() is False

    def test_user_has_permission(self):
        """Test has_permission method with role hierarchy."""
        super_admin = User(
            id=uuid4(),
            email=Email(value="admin@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Admin",
            last_name="User",
            role=UserRole.SUPER_ADMIN,
        )

        store_owner = User(
            id=uuid4(),
            email=Email(value="owner@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Store",
            last_name="Owner",
            role=UserRole.STORE_OWNER,
        )

        customer = User(
            id=uuid4(),
            email=Email(value="customer@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Jane",
            last_name="Customer",
            role=UserRole.CUSTOMER,
        )

        # Super admin has all permissions
        assert super_admin.has_permission(UserRole.CUSTOMER) is True
        assert super_admin.has_permission(UserRole.STORE_OWNER) is True
        assert super_admin.has_permission(UserRole.SUPER_ADMIN) is True

        # Store owner has store_owner and below
        assert store_owner.has_permission(UserRole.CUSTOMER) is True
        assert store_owner.has_permission(UserRole.STORE_OWNER) is True
        assert store_owner.has_permission(UserRole.SUPER_ADMIN) is False

        # Customer only has customer permission
        assert customer.has_permission(UserRole.CUSTOMER) is True
        assert customer.has_permission(UserRole.STORE_OWNER) is False
        assert customer.has_permission(UserRole.SUPER_ADMIN) is False

    def test_user_update_last_login(self):
        """Test update_last_login method."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
        )

        assert user.last_login_at is None
        user.update_last_login()
        assert user.last_login_at is not None
        assert isinstance(user.last_login_at, datetime)

    def test_user_touch_updates_timestamp(self):
        """Test that touch() updates updated_at."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
        )

        original_updated_at = user.updated_at
        user.touch()
        # updated_at should be equal or later
        assert user.updated_at >= original_updated_at

    def test_user_serialization(self):
        """Test user serialization to dict."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="John",
            last_name="Doe",
            role=UserRole.CUSTOMER,
            status=UserStatus.ACTIVE,
        )

        data = user.model_dump()
        assert data["first_name"] == "John"
        assert data["last_name"] == "Doe"
        assert data["role"] == UserRole.CUSTOMER
        assert data["status"] == UserStatus.ACTIVE

    def test_user_from_attributes(self):
        """Test user creation from ORM-like attributes."""
        # Simulate ORM object with __dict__
        class MockORM:
            def __init__(self):
                self.id = uuid4()
                self.email = Email(value="test@example.com")  # Must be Email object
                self.hashed_password = "$2b$12$hashedpassword"
                self.first_name = "John"
                self.last_name = "Doe"
                self.role = UserRole.CUSTOMER
                self.status = UserStatus.ACTIVE
                self.phone = None
                self.avatar_url = None
                self.email_verified_at = None
                self.last_login_at = None
                self.created_at = datetime.utcnow()
                self.updated_at = datetime.utcnow()

        # The from_attributes config allows creating from ORM objects
        # This is tested through model_validate with from_attributes=True
        mock = MockORM()
        user = User.model_validate(mock, from_attributes=True)
        assert user.first_name == "John"
        assert user.last_name == "Doe"
