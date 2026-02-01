"""Unit tests for store use cases."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.use_cases.stores.create_store import CreateStoreUseCase
from src.application.use_cases.stores.get_store import GetStoreUseCase
from src.application.use_cases.stores.list_stores import ListStoresUseCase
from src.application.use_cases.stores.update_store import UpdateStoreUseCase
from src.application.use_cases.stores.delete_store import DeleteStoreUseCase
from src.core.entities.store import Store, StoreStatus
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.value_objects.money import Currency


class TestCreateStoreUseCase:
    """Tests for CreateStoreUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_store_repo = MagicMock()
        self.mock_store_repo.slug_exists = AsyncMock(return_value=False)
        self.mock_store_repo.subdomain_exists = AsyncMock(return_value=False)
        self.mock_store_repo.create = AsyncMock()

        self.mock_tenant_service = MagicMock()
        self.mock_tenant_service.create_tenant = AsyncMock()

        self.use_case = CreateStoreUseCase(
            store_repository=self.mock_store_repo,
            tenant_service=self.mock_tenant_service,
        )

        self.user_id = uuid4()

    @pytest.mark.asyncio
    async def test_create_store_success(self):
        """Test successful store creation."""
        tenant_id = uuid4()
        self.mock_tenant_service.create_tenant.return_value = MagicMock(id=tenant_id)

        created_store = Store(
            id=uuid4(),
            owner_id=self.user_id,
            name="My Store",
            slug="my-store",
            subdomain="mystore",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
            tenant_id=tenant_id,
        )
        self.mock_store_repo.create.return_value = created_store

        from src.application.dto.store import CreateStoreDTO
        dto = CreateStoreDTO(
            name="My Store",
            subdomain="mystore",
            description="A great store",
        )

        result = await self.use_case.execute(dto=dto, owner_id=self.user_id)

        assert result is not None
        assert result.name == "My Store"
        self.mock_store_repo.create.assert_called_once()
        self.mock_tenant_service.create_tenant.assert_called_once()


class TestGetStoreUseCase:
    """Tests for GetStoreUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_store_repo = MagicMock()
        self.mock_store_repo.get_by_id = AsyncMock()

        self.use_case = GetStoreUseCase(store_repository=self.mock_store_repo)

        self.store_id = uuid4()
        self.sample_store = Store(
            id=self.store_id,
            owner_id=uuid4(),
            name="Test Store",
            slug="test-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
        )

    @pytest.mark.asyncio
    async def test_get_store_success(self):
        """Test successful store retrieval."""
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        result = await self.use_case.execute(store_id=self.store_id)

        assert result is not None
        assert result.id == self.store_id
        assert result.name == "Test Store"

    @pytest.mark.asyncio
    async def test_get_store_not_found(self):
        """Test store retrieval when not found."""
        self.mock_store_repo.get_by_id.return_value = None

        with pytest.raises(EntityNotFoundError):
            await self.use_case.execute(store_id=uuid4())


class TestListStoresUseCase:
    """Tests for ListStoresUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_store_repo = MagicMock()
        self.mock_store_repo.get_by_owner = AsyncMock()
        self.mock_store_repo.get_all = AsyncMock()
        self.mock_store_repo.count = AsyncMock()

        self.use_case = ListStoresUseCase(store_repository=self.mock_store_repo)

        self.user_id = uuid4()

    @pytest.mark.asyncio
    async def test_list_stores_success(self):
        """Test successful store listing."""
        stores = [
            Store(
                id=uuid4(),
                owner_id=self.user_id,
                name=f"Store {i}",
                slug=f"store-{i}",
                status=StoreStatus.ACTIVE,
                default_currency=Currency.USD,
            )
            for i in range(3)
        ]
        self.mock_store_repo.get_all.return_value = stores
        self.mock_store_repo.count.return_value = 3

        result = await self.use_case.execute()

        assert len(result.items) == 3
        assert result.total == 3

    @pytest.mark.asyncio
    async def test_list_stores_empty(self):
        """Test listing stores when none exist."""
        self.mock_store_repo.get_all.return_value = []
        self.mock_store_repo.count.return_value = 0

        result = await self.use_case.execute()

        assert len(result.items) == 0
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_list_stores_by_owner(self):
        """Test listing stores by owner."""
        stores = [
            Store(
                id=uuid4(),
                owner_id=self.user_id,
                name=f"Store {i}",
                slug=f"store-{i}",
                status=StoreStatus.ACTIVE,
                default_currency=Currency.USD,
            )
            for i in range(2)
        ]
        self.mock_store_repo.get_by_owner.return_value = stores

        result = await self.use_case.by_owner(owner_id=self.user_id)

        assert len(result.items) == 2


class TestUpdateStoreUseCase:
    """Tests for UpdateStoreUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_store_repo = MagicMock()
        self.mock_store_repo.get_by_id = AsyncMock()
        self.mock_store_repo.update = AsyncMock()

        self.use_case = UpdateStoreUseCase(store_repository=self.mock_store_repo)

        self.user_id = uuid4()
        self.store_id = uuid4()
        self.sample_store = Store(
            id=self.store_id,
            owner_id=self.user_id,
            name="Original Store",
            slug="original-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
        )

    @pytest.mark.asyncio
    async def test_update_store_success(self):
        """Test successful store update."""
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        updated_store = Store(
            id=self.store_id,
            owner_id=self.user_id,
            name="Updated Store",
            slug="original-store",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
        )
        self.mock_store_repo.update.return_value = updated_store

        from src.application.dto.store import UpdateStoreDTO
        dto = UpdateStoreDTO(name="Updated Store")

        result = await self.use_case.execute(
            store_id=self.store_id,
            dto=dto,
            user_id=self.user_id,
        )

        assert result.name == "Updated Store"

    @pytest.mark.asyncio
    async def test_update_store_not_owner(self):
        """Test update by non-owner."""
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        from src.application.dto.store import UpdateStoreDTO
        dto = UpdateStoreDTO(name="Hacked Store")

        with pytest.raises(AuthorizationError):
            await self.use_case.execute(
                store_id=self.store_id,
                dto=dto,
                user_id=uuid4(),  # Different user
            )


class TestDeleteStoreUseCase:
    """Tests for DeleteStoreUseCase."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_store_repo = MagicMock()
        self.mock_store_repo.get_by_id = AsyncMock()
        self.mock_store_repo.delete = AsyncMock(return_value=True)

        self.use_case = DeleteStoreUseCase(store_repository=self.mock_store_repo)

        self.user_id = uuid4()
        self.store_id = uuid4()
        self.sample_store = Store(
            id=self.store_id,
            owner_id=self.user_id,
            name="Store to Delete",
            slug="store-to-delete",
            status=StoreStatus.ACTIVE,
            default_currency=Currency.USD,
        )

    @pytest.mark.asyncio
    async def test_delete_store_success(self):
        """Test successful store deletion."""
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        await self.use_case.execute(store_id=self.store_id, user_id=self.user_id)

        self.mock_store_repo.delete.assert_called_once_with(self.store_id)

    @pytest.mark.asyncio
    async def test_delete_store_not_owner(self):
        """Test deletion by non-owner."""
        self.mock_store_repo.get_by_id.return_value = self.sample_store

        with pytest.raises(AuthorizationError):
            await self.use_case.execute(
                store_id=self.store_id,
                user_id=uuid4(),  # Different user
            )

    @pytest.mark.asyncio
    async def test_delete_store_not_found(self):
        """Test deletion of non-existent store."""
        self.mock_store_repo.get_by_id.return_value = None

        with pytest.raises(EntityNotFoundError):
            await self.use_case.execute(
                store_id=self.store_id,
                user_id=self.user_id,
            )
