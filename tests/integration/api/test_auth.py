"""Integration tests for authentication endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestAuthEndpoints:
    """Tests for authentication API endpoints."""

    async def test_register_user(self, client: AsyncClient, sample_user_data: dict):
        """Test user registration endpoint."""
        response = await client.post("/api/v1/auth/register", json=sample_user_data)
        
        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "user" in data["data"]
        assert "tokens" in data["data"]
        assert data["data"]["user"]["email"] == sample_user_data["email"]

    async def test_register_duplicate_email(
        self, client: AsyncClient, sample_user_data: dict
    ):
        """Test registration with duplicate email fails."""
        # First registration
        await client.post("/api/v1/auth/register", json=sample_user_data)
        
        # Second registration with same email
        response = await client.post("/api/v1/auth/register", json=sample_user_data)
        
        assert response.status_code in [400, 409, 422]

    async def test_login_success(self, client: AsyncClient, sample_user_data: dict):
        """Test successful login."""
        # Register first
        await client.post("/api/v1/auth/register", json=sample_user_data)
        
        # Login
        login_data = {
            "email": sample_user_data["email"],
            "password": sample_user_data["password"],
        }
        response = await client.post("/api/v1/auth/login", json=login_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "tokens" in data["data"]
        assert "access_token" in data["data"]["tokens"]

    async def test_login_invalid_credentials(self, client: AsyncClient):
        """Test login with invalid credentials."""
        login_data = {
            "email": "nonexistent@example.com",
            "password": "wrongpassword",
        }
        response = await client.post("/api/v1/auth/login", json=login_data)
        
        assert response.status_code in [401, 404]

    async def test_get_current_user(self, client: AsyncClient, sample_user_data: dict):
        """Test getting current user profile."""
        # Register and get token
        register_response = await client.post(
            "/api/v1/auth/register", json=sample_user_data
        )
        token = register_response.json()["data"]["tokens"]["access_token"]
        
        # Get current user
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["email"] == sample_user_data["email"]

    async def test_get_current_user_unauthorized(self, client: AsyncClient):
        """Test getting current user without token fails."""
        response = await client.get("/api/v1/auth/me")
        
        assert response.status_code in [401, 403]
