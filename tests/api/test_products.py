"""Tests for product routes."""

from uuid import uuid4

import pytest
from httpx import AsyncClient


class TestProductRoutes:
    """Tests for /stores/{store_id}/products endpoints."""

    @pytest.mark.asyncio
    async def test_create_product_success(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
        sample_product_data: dict,
    ):
        """Test successful product creation."""
        # Register user
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # Create store
        store_response = await client.post(
            "/api/v1/stores/",
            json=sample_store_data,
            headers=headers,
        )
        store_id = store_response.json()["data"]["id"]

        # Create product
        response = await client.post(
            f"/api/v1/stores/{store_id}/products/",
            json=sample_product_data,
            headers=headers,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert data["data"]["name"] == sample_product_data["name"]

    @pytest.mark.asyncio
    async def test_create_product_unauthorized(
        self,
        client: AsyncClient,
        sample_product_data: dict,
    ):
        """Test product creation without authentication."""
        store_id = uuid4()
        response = await client.post(
            f"/api/v1/stores/{store_id}/products/",
            json=sample_product_data,
        )

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_product_store_not_found(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_product_data: dict,
    ):
        """Test product creation for non-existent store."""
        # Register user
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # Try to create product for non-existent store
        fake_store_id = uuid4()
        response = await client.post(
            f"/api/v1/stores/{fake_store_id}/products",
            json=sample_product_data,
            headers=headers,
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_products(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
        sample_product_data: dict,
    ):
        """Test listing products."""
        # Setup: Register, create store, create products
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        store_response = await client.post(
            "/api/v1/stores/",
            json=sample_store_data,
            headers=headers,
        )
        store_id = store_response.json()["data"]["id"]

        # Create multiple products
        for i in range(3):
            product_data = sample_product_data.copy()
            product_data["name"] = f"Product {i}"
            product_data["sku"] = f"SKU-{i}"
            await client.post(
                f"/api/v1/stores/{store_id}/products/",
                json=product_data,
                headers=headers,
            )

        # List products
        response = await client.get(f"/api/v1/stores/{store_id}/products/")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["items"]) == 3

    @pytest.mark.asyncio
    async def test_list_products_pagination(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
        sample_product_data: dict,
    ):
        """Test product listing pagination."""
        # Setup
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        store_response = await client.post(
            "/api/v1/stores/",
            json=sample_store_data,
            headers=headers,
        )
        store_id = store_response.json()["data"]["id"]

        # Create 5 products
        for i in range(5):
            product_data = sample_product_data.copy()
            product_data["name"] = f"Product {i}"
            product_data["sku"] = f"SKU-{i}"
            await client.post(
                f"/api/v1/stores/{store_id}/products/",
                json=product_data,
                headers=headers,
            )

        # Get first page with limit 2
        response = await client.get(
            f"/api/v1/stores/{store_id}/products/",
            params={"page": 1, "limit": 2},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["items"]) == 2
        assert data["data"]["total"] == 5
        assert data["data"]["total_pages"] == 3

    @pytest.mark.asyncio
    async def test_get_product_by_id(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
        sample_product_data: dict,
    ):
        """Test getting a product by ID."""
        # Setup
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        store_response = await client.post(
            "/api/v1/stores/",
            json=sample_store_data,
            headers=headers,
        )
        store_id = store_response.json()["data"]["id"]

        # Create product
        create_response = await client.post(
            f"/api/v1/stores/{store_id}/products/",
            json=sample_product_data,
            headers=headers,
        )
        product_id = create_response.json()["data"]["id"]

        # Get product
        response = await client.get(f"/api/v1/stores/{store_id}/products/{product_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["id"] == product_id

    @pytest.mark.asyncio
    async def test_get_product_not_found(self, client: AsyncClient):
        """Test getting non-existent product."""
        store_id = uuid4()
        product_id = uuid4()
        response = await client.get(f"/api/v1/stores/{store_id}/products/{product_id}")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_product(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
        sample_product_data: dict,
    ):
        """Test updating a product."""
        # Setup
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        store_response = await client.post(
            "/api/v1/stores/",
            json=sample_store_data,
            headers=headers,
        )
        store_id = store_response.json()["data"]["id"]

        create_response = await client.post(
            f"/api/v1/stores/{store_id}/products/",
            json=sample_product_data,
            headers=headers,
        )
        product_id = create_response.json()["data"]["id"]

        # Update product
        update_data = {"name": "Updated Product Name", "price": 2999}
        response = await client.patch(
            f"/api/v1/stores/{store_id}/products/{product_id}",
            json=update_data,
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["name"] == "Updated Product Name"

    @pytest.mark.asyncio
    async def test_update_product_unauthorized(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
        sample_product_data: dict,
    ):
        """Test updating product without proper authorization."""
        # Create first user and store/product
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        store_response = await client.post(
            "/api/v1/stores/",
            json=sample_store_data,
            headers=headers,
        )
        store_id = store_response.json()["data"]["id"]

        create_response = await client.post(
            f"/api/v1/stores/{store_id}/products/",
            json=sample_product_data,
            headers=headers,
        )
        product_id = create_response.json()["data"]["id"]

        # Create second user
        user2_data = sample_user_data.copy()
        user2_data["email"] = "user2@example.com"
        register2_response = await client.post("/api/v1/auth/register", json=user2_data)
        tokens2 = register2_response.json()["data"]["tokens"]
        headers2 = {"Authorization": f"Bearer {tokens2['access_token']}"}

        # Try to update product with different user
        update_data = {"name": "Hacked Name"}
        response = await client.patch(
            f"/api/v1/stores/{store_id}/products/{product_id}",
            json=update_data,
            headers=headers2,
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_product(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
        sample_product_data: dict,
    ):
        """Test deleting a product."""
        # Setup
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        store_response = await client.post(
            "/api/v1/stores/",
            json=sample_store_data,
            headers=headers,
        )
        store_id = store_response.json()["data"]["id"]

        create_response = await client.post(
            f"/api/v1/stores/{store_id}/products/",
            json=sample_product_data,
            headers=headers,
        )
        product_id = create_response.json()["data"]["id"]

        # Delete product
        response = await client.delete(
            f"/api/v1/stores/{store_id}/products/{product_id}",
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["deleted"] is True

        # Verify product is deleted
        get_response = await client.get(f"/api/v1/stores/{store_id}/products/{product_id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_search_products(
        self,
        client: AsyncClient,
        sample_user_data: dict,
        sample_store_data: dict,
        sample_product_data: dict,
    ):
        """Test searching products."""
        # Setup
        register_response = await client.post("/api/v1/auth/register", json=sample_user_data)
        tokens = register_response.json()["data"]["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        store_response = await client.post(
            "/api/v1/stores/",
            json=sample_store_data,
            headers=headers,
        )
        store_id = store_response.json()["data"]["id"]

        # Create products with different names
        for name in ["Apple iPhone", "Samsung Galaxy", "Google Pixel"]:
            product_data = sample_product_data.copy()
            product_data["name"] = name
            product_data["sku"] = f"SKU-{name.replace(' ', '-')}"
            await client.post(
                f"/api/v1/stores/{store_id}/products/",
                json=product_data,
                headers=headers,
            )

        # Search for "Samsung"
        response = await client.get(
            f"/api/v1/stores/{store_id}/products/",
            params={"search": "Samsung"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["items"]) == 1
        assert "Samsung" in data["data"]["items"][0]["name"]
