"""Tests for store routes."""

import pytest
from httpx import AsyncClient
from uuid import uuid4


class TestStoreRoutes:
    """Tests for /stores endpoints."""

    @pytest.mark.asyncio
    async def test_create_store_success(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
    ):
        """Test successful store creation."""
        # Register user
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # Create store
        response = await client.post("/api/v1/stores", json=sample_store_data, headers=headers)

        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert data["data"]["name"] == sample_store_data["name"]
        assert data["data"]["status"] == "active"

    @pytest.mark.asyncio
    async def test_create_store_unauthorized(
        self,
        client: AsyncClient,
        sample_store_data: dict,
    ):
        """Test store creation without authentication."""
        response = await client.post("/api/v1/stores", json=sample_store_data)

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_store_duplicate_slug(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
    ):
        """Test store creation with duplicate slug."""
        # Register user
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # Create first store
        await client.post("/api/v1/stores", json=sample_store_data, headers=headers)

        # Try to create another store with same slug (should generate unique slug)
        response = await client.post("/api/v1/stores", json=sample_store_data, headers=headers)

        # Should still succeed with a modified slug
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_list_stores(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
    ):
        """Test listing user's stores."""
        # Register user
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # Create multiple stores
        for i in range(3):
            store_data = sample_store_data.copy()
            store_data["name"] = f"Store {i}"
            await client.post("/api/v1/stores", json=store_data, headers=headers)

        # List stores
        response = await client.get("/api/v1/stores", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["items"]) == 3

    @pytest.mark.asyncio
    async def test_get_store_by_id(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
    ):
        """Test getting a store by ID."""
        # Setup
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        create_response = await client.post("/api/v1/stores", json=sample_store_data, headers=headers)
        store_id = create_response.json()["data"]["id"]

        # Get store
        response = await client.get(f"/api/v1/stores/{store_id}", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["id"] == store_id

    @pytest.mark.asyncio
    async def test_get_store_not_found(
        self,
        client: AsyncClient,
        sample_user_data: dict,
    ):
        """Test getting non-existent store."""
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        fake_id = uuid4()
        response = await client.get(f"/api/v1/stores/{fake_id}", headers=headers)

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_store(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
    ):
        """Test updating a store."""
        # Setup
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        create_response = await client.post("/api/v1/stores", json=sample_store_data, headers=headers)
        store_id = create_response.json()["data"]["id"]

        # Update store
        update_data = {"name": "Updated Store Name", "description": "Updated description"}
        response = await client.patch(f"/api/v1/stores/{store_id}", json=update_data, headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["name"] == "Updated Store Name"
        assert data["data"]["description"] == "Updated description"

    @pytest.mark.asyncio
    async def test_update_store_not_owner(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
    ):
        """Test updating store by non-owner."""
        # Create first user and store
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        create_response = await client.post("/api/v1/stores", json=sample_store_data, headers=headers)
        store_id = create_response.json()["data"]["id"]

        # Create second user
        user2_data = sample_user_data.copy()
        user2_data["email"] = "user2@example.com"
        register2_response = await client.post("/api/v1/auth/register", json=user2_data)
        tokens2 = register2_response.json()["data"]["tokens"]
        headers2 = {"Authorization": f"Bearer {tokens2['access_token']}"}

        # Try to update store with different user
        update_data = {"name": "Hacked Name"}
        response = await client.patch(f"/api/v1/stores/{store_id}", json=update_data, headers=headers2)

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_store(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
    ):
        """Test deleting a store."""
        # Setup
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        create_response = await client.post("/api/v1/stores", json=sample_store_data, headers=headers)
        store_id = create_response.json()["data"]["id"]

        # Delete store
        response = await client.delete(f"/api/v1/stores/{store_id}", headers=headers)

        assert response.status_code == 200

        # Verify store is deleted
        get_response = await client.get(f"/api/v1/stores/{store_id}", headers=headers)
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_store_validation_name_too_short(
        self,
        client: AsyncClient,
        sample_user_data: dict,
    ):
        """Test store creation with invalid name."""
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        store_data = {"name": ""}  # Empty name
        response = await client.post("/api/v1/stores", json=store_data, headers=headers)

        assert response.status_code == 422
