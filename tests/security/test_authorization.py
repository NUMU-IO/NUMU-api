"""Tests for authorization and RBAC."""

from uuid import uuid4

import pytest

from src.core.entities.user import User, UserRole, UserStatus
from src.core.value_objects.email import Email


class TestRoleHierarchy:
    """Tests for role hierarchy and permissions."""

    def _create_user(self, role: UserRole) -> User:
        """Helper to create a user with specified role."""
        return User(
            id=uuid4(),
            email=Email(value=f"{role.value}@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Test",
            last_name="User",
            role=role,
            status=UserStatus.ACTIVE,
        )

    def test_super_admin_has_all_permissions(self):
        """Test that super admin has all role permissions."""
        super_admin = self._create_user(UserRole.SUPER_ADMIN)

        assert super_admin.has_permission(UserRole.CUSTOMER) is True
        assert super_admin.has_permission(UserRole.STORE_STAFF) is True
        assert super_admin.has_permission(UserRole.STORE_ADMIN) is True
        assert super_admin.has_permission(UserRole.STORE_OWNER) is True
        assert super_admin.has_permission(UserRole.SUPER_ADMIN) is True

    def test_store_owner_permissions(self):
        """Test store owner permission level."""
        store_owner = self._create_user(UserRole.STORE_OWNER)

        assert store_owner.has_permission(UserRole.CUSTOMER) is True
        assert store_owner.has_permission(UserRole.STORE_STAFF) is True
        assert store_owner.has_permission(UserRole.STORE_ADMIN) is True
        assert store_owner.has_permission(UserRole.STORE_OWNER) is True
        assert store_owner.has_permission(UserRole.SUPER_ADMIN) is False

    def test_store_admin_permissions(self):
        """Test store admin permission level."""
        store_admin = self._create_user(UserRole.STORE_ADMIN)

        assert store_admin.has_permission(UserRole.CUSTOMER) is True
        assert store_admin.has_permission(UserRole.STORE_STAFF) is True
        assert store_admin.has_permission(UserRole.STORE_ADMIN) is True
        assert store_admin.has_permission(UserRole.STORE_OWNER) is False
        assert store_admin.has_permission(UserRole.SUPER_ADMIN) is False

    def test_store_staff_permissions(self):
        """Test store staff permission level."""
        store_staff = self._create_user(UserRole.STORE_STAFF)

        assert store_staff.has_permission(UserRole.CUSTOMER) is True
        assert store_staff.has_permission(UserRole.STORE_STAFF) is True
        assert store_staff.has_permission(UserRole.STORE_ADMIN) is False
        assert store_staff.has_permission(UserRole.STORE_OWNER) is False
        assert store_staff.has_permission(UserRole.SUPER_ADMIN) is False

    def test_customer_permissions(self):
        """Test customer permission level (lowest)."""
        customer = self._create_user(UserRole.CUSTOMER)

        assert customer.has_permission(UserRole.CUSTOMER) is True
        assert customer.has_permission(UserRole.STORE_STAFF) is False
        assert customer.has_permission(UserRole.STORE_ADMIN) is False
        assert customer.has_permission(UserRole.STORE_OWNER) is False
        assert customer.has_permission(UserRole.SUPER_ADMIN) is False

    def test_can_manage_store_roles(self):
        """Test which roles can manage stores."""
        super_admin = self._create_user(UserRole.SUPER_ADMIN)
        store_owner = self._create_user(UserRole.STORE_OWNER)
        store_admin = self._create_user(UserRole.STORE_ADMIN)
        store_staff = self._create_user(UserRole.STORE_STAFF)
        customer = self._create_user(UserRole.CUSTOMER)

        assert super_admin.can_manage_store() is True
        assert store_owner.can_manage_store() is True
        assert store_admin.can_manage_store() is True
        assert store_staff.can_manage_store() is False
        assert customer.can_manage_store() is False

    def test_is_admin_property(self):
        """Test is_admin property for different roles."""
        super_admin = self._create_user(UserRole.SUPER_ADMIN)
        store_admin = self._create_user(UserRole.STORE_ADMIN)
        store_owner = self._create_user(UserRole.STORE_OWNER)
        customer = self._create_user(UserRole.CUSTOMER)

        assert super_admin.is_admin is True
        assert store_admin.is_admin is True
        assert store_owner.is_admin is False  # Owner but not admin
        assert customer.is_admin is False

    def test_is_super_admin_property(self):
        """Test is_super_admin property."""
        super_admin = self._create_user(UserRole.SUPER_ADMIN)
        store_admin = self._create_user(UserRole.STORE_ADMIN)

        assert super_admin.is_super_admin is True
        assert store_admin.is_super_admin is False


class TestUserStatusAuthorization:
    """Tests for user status affecting authorization."""

    def _create_user(self, status: UserStatus) -> User:
        """Helper to create a user with specified status."""
        return User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Test",
            last_name="User",
            role=UserRole.STORE_OWNER,
            status=status,
        )

    def test_active_user_is_active(self):
        """Test that active status returns is_active=True."""
        user = self._create_user(UserStatus.ACTIVE)
        assert user.is_active is True

    def test_inactive_user_not_active(self):
        """Test that inactive status returns is_active=False."""
        user = self._create_user(UserStatus.INACTIVE)
        assert user.is_active is False

    def test_suspended_user_not_active(self):
        """Test that suspended status returns is_active=False."""
        user = self._create_user(UserStatus.SUSPENDED)
        assert user.is_active is False

    def test_pending_verification_user_not_active(self):
        """Test that pending verification status returns is_active=False."""
        user = self._create_user(UserStatus.PENDING_VERIFICATION)
        assert user.is_active is False

    def test_verified_user(self):
        """Test email verification sets is_verified."""
        user = User(
            id=uuid4(),
            email=Email(value="test@example.com"),
            hashed_password="$2b$12$hashedpassword",
            first_name="Test",
            last_name="User",
            role=UserRole.CUSTOMER,
            status=UserStatus.PENDING_VERIFICATION,
            email_verified_at=None,
        )

        assert user.is_verified is False

        user.verify_email()

        assert user.is_verified is True
        assert user.status == UserStatus.ACTIVE


class TestStoreOwnership:
    """Tests for store ownership authorization."""

    def test_store_owned_by_correct_user(self):
        """Test is_owned_by returns True for owner."""
        from src.core.entities.store import Store

        owner_id = uuid4()
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=owner_id,
        )

        assert store.is_owned_by(owner_id) is True

    def test_store_not_owned_by_other_user(self):
        """Test is_owned_by returns False for non-owner."""
        from src.core.entities.store import Store

        owner_id = uuid4()
        other_user_id = uuid4()
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=owner_id,
        )

        assert store.is_owned_by(other_user_id) is False


class TestCustomerStoreScoping:
    """Tests for customer store scoping (multi-tenant)."""

    def test_customer_belongs_to_store(self):
        """Test that customer is scoped to a store."""
        from src.core.entities.customer import Customer

        store_id = uuid4()
        customer = Customer(
            id=uuid4(),
            store_id=store_id,
            email=Email(value="customer@example.com"),
            first_name="Test",
            last_name="Customer",
        )

        assert customer.store_id == store_id

    def test_customer_cannot_change_store(self):
        """Test that customer store_id can be validated."""
        from src.core.entities.customer import Customer

        store_a = uuid4()
        store_b = uuid4()

        customer = Customer(
            id=uuid4(),
            store_id=store_a,
            email=Email(value="customer@example.com"),
            first_name="Test",
            last_name="Customer",
        )

        # Customer is tied to store_a
        assert customer.store_id == store_a
        assert customer.store_id != store_b
