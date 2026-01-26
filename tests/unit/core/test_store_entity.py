"""Unit tests for Store entity."""

from uuid import uuid4

import pytest

from src.core.entities.store import Store, StoreStatus
from src.core.value_objects.money import Currency


class TestStoreEntity:
    """Tests for the Store entity."""

    def test_create_store_with_valid_data(self):
        """Test creating a store with valid data."""
        owner_id = uuid4()
        store = Store(
            id=uuid4(),
            name="My Test Store",
            slug="my-test-store",
            owner_id=owner_id,
            description="A test store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
        )

        assert store.name == "My Test Store"
        assert store.slug == "my-test-store"
        assert store.owner_id == owner_id
        assert store.status == StoreStatus.ACTIVE
        assert store.default_currency == Currency.USD

    def test_store_is_active(self):
        """Test is_active property."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            status=StoreStatus.ACTIVE,
        )

        assert store.is_active is True

        inactive_store = Store(
            id=uuid4(),
            name="Inactive Store",
            slug="inactive-store",
            owner_id=uuid4(),
            status=StoreStatus.INACTIVE,
        )

        assert inactive_store.is_active is False

    def test_store_is_suspended(self):
        """Test is_suspended property."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            status=StoreStatus.SUSPENDED,
        )

        assert store.is_suspended is True

    def test_store_is_pending(self):
        """Test is_pending property."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            status=StoreStatus.PENDING_APPROVAL,
        )

        assert store.is_pending is True

    def test_store_activate(self):
        """Test activate method."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            status=StoreStatus.INACTIVE,
        )

        store.activate()
        assert store.status == StoreStatus.ACTIVE
        assert store.is_active is True

    def test_store_suspend(self):
        """Test suspend method."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            status=StoreStatus.ACTIVE,
        )

        store.suspend(reason="Policy violation")
        assert store.status == StoreStatus.SUSPENDED
        assert store.settings.get("suspension_reason") == "Policy violation"
        assert "suspended_at" in store.settings

    def test_store_deactivate(self):
        """Test deactivate method."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            status=StoreStatus.ACTIVE,
        )

        store.deactivate()
        assert store.status == StoreStatus.INACTIVE

    def test_store_approve(self):
        """Test approve method."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            status=StoreStatus.PENDING_APPROVAL,
        )

        store.approve()
        assert store.status == StoreStatus.ACTIVE
        assert "approved_at" in store.settings

    def test_store_approve_already_active(self):
        """Test approve on already active store doesn't change status."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            status=StoreStatus.ACTIVE,
        )

        store.approve()
        # Should not add approved_at since it wasn't pending
        assert "approved_at" not in store.settings

    def test_store_update_settings(self):
        """Test update_settings method."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
        )

        store.update_settings(
            theme="dark",
            enable_reviews=True,
            max_products=100,
        )

        assert store.settings["theme"] == "dark"
        assert store.settings["enable_reviews"] is True
        assert store.settings["max_products"] == 100

    def test_store_set_social_link(self):
        """Test set_social_link method."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
        )

        store.set_social_link("twitter", "https://twitter.com/teststore")
        store.set_social_link("instagram", "https://instagram.com/teststore")

        assert store.social_links["twitter"] == "https://twitter.com/teststore"
        assert store.social_links["instagram"] == "https://instagram.com/teststore"

    def test_store_remove_social_link(self):
        """Test remove_social_link method."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            social_links={"twitter": "https://twitter.com/teststore"},
        )

        store.remove_social_link("twitter")
        assert "twitter" not in store.social_links

    def test_store_remove_nonexistent_social_link(self):
        """Test remove_social_link with nonexistent platform doesn't raise."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
        )

        # Should not raise
        store.remove_social_link("facebook")

    def test_store_is_owned_by(self):
        """Test is_owned_by method."""
        owner_id = uuid4()
        other_user_id = uuid4()

        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=owner_id,
        )

        assert store.is_owned_by(owner_id) is True
        assert store.is_owned_by(other_user_id) is False

    def test_store_with_tenant_id(self):
        """Test store with tenant_id."""
        tenant_id = uuid4()
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            tenant_id=tenant_id,
        )

        assert store.tenant_id == tenant_id

    def test_store_serialization(self):
        """Test store serialization to dict."""
        store = Store(
            id=uuid4(),
            name="Test Store",
            slug="test-store",
            owner_id=uuid4(),
            description="A test store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
        )

        data = store.model_dump()
        assert data["name"] == "Test Store"
        assert data["slug"] == "test-store"
        assert data["description"] == "A test store"
        assert data["status"] == StoreStatus.ACTIVE
        assert data["default_currency"] == Currency.USD
