"""Unit tests for CustomerRepository."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.infrastructure.repositories.customer_repository import CustomerRepository


class TestCustomerRepository:
    """Tests for CustomerRepository."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_session = MagicMock()
        self.mock_session.execute = AsyncMock()
        self.mock_session.add = MagicMock()
        self.mock_session.flush = AsyncMock()
        self.mock_session.refresh = AsyncMock()
        self.mock_session.delete = AsyncMock()
        self.repository = CustomerRepository(self.mock_session)

    def _create_mock_customer_model(self, **kwargs):
        """Create a mock customer model."""
        model = MagicMock()
        model.id = kwargs.get("id", uuid4())
        model.store_id = kwargs.get("store_id", uuid4())
        model.user_id = kwargs.get("user_id", None)
        model.email = kwargs.get("email", "customer@example.com")
        model.password_hash = kwargs.get("password_hash", "hashed")
        model.first_name = kwargs.get("first_name", "John")
        model.last_name = kwargs.get("last_name", "Doe")
        model.phone = kwargs.get("phone", "+201234567890")
        model.is_verified = kwargs.get("is_verified", True)
        model.accepts_marketing = kwargs.get("accepts_marketing", False)
        model.total_orders = kwargs.get("total_orders", 0)
        model.total_spent = kwargs.get("total_spent", 0)
        model.notes = kwargs.get("notes", None)
        model.tags = kwargs.get("tags", [])
        model.default_address_id = kwargs.get("default_address_id", None)
        model.extra_data = kwargs.get("extra_data", {})
        model.created_at = kwargs.get("created_at", datetime.utcnow())
        model.updated_at = kwargs.get("updated_at", datetime.utcnow())
        return model

    @pytest.mark.asyncio
    async def test_get_by_id_found(self):
        """Test getting customer by ID when it exists."""
        customer_id = uuid4()
        mock_model = self._create_mock_customer_model(id=customer_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_id(customer_id)

        assert result is not None
        assert result.id == customer_id

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self):
        """Test getting customer by ID when it doesn't exist."""
        customer_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_id(customer_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_email(self):
        """Test getting customer by email."""
        store_id = uuid4()
        email = "customer@example.com"
        mock_model = self._create_mock_customer_model(store_id=store_id, email=email)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_email(store_id, email)

        assert result is not None
        assert result.email.value == email

    @pytest.mark.asyncio
    async def test_get_by_store(self):
        """Test getting customers by store ID."""
        store_id = uuid4()

        mock_models = [
            self._create_mock_customer_model(store_id=store_id, email=f"customer{i}@example.com")
            for i in range(5)
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_models
        self.mock_session.execute.return_value = mock_result

        results = await self.repository.get_by_store(store_id)

        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_delete_existing(self):
        """Test deleting an existing customer."""
        customer_id = uuid4()
        mock_model = self._create_mock_customer_model(id=customer_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.delete(customer_id)

        assert result is True
        self.mock_session.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_not_found(self):
        """Test deleting a non-existent customer."""
        customer_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.delete(customer_id)

        assert result is False

    @pytest.mark.asyncio
    async def test_count_by_store(self):
        """Test counting customers by store."""
        store_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar.return_value = 25
        self.mock_session.execute.return_value = mock_result

        count = await self.repository.count_by_store(store_id)

        assert count == 25
