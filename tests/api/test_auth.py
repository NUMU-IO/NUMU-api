"""Tests for authentication routes."""

import pytest
from httpx import AsyncClient


class TestAuthRoutes:
    """Tests for /api/v1/auth endpoints."""

    @pytest.mark.asyncio
    async def test_register_success(self, client: AsyncClient, sample_user_data: dict):
        """Test successful user registration."""
        response = await client.post("/api/v1/auth/register", json=sample_user_data)

        # Should return 201 Created
        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert "data" in data
        assert "user" in data["data"]
        assert "tokens" in data["data"]
        assert data["data"]["user"]["email"] == sample_user_data["email"]
        assert data["data"]["tokens"]["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_register_duplicate_email(
        self, client: AsyncClient, sample_user_data: dict
    ):
        """Test registration with duplicate email."""
        # Register first user
        await client.post("/api/v1/auth/register", json=sample_user_data)

        # Try to register again with same email
        response = await client.post("/api/v1/auth/register", json=sample_user_data)

        assert response.status_code == 400
        data = response.json()
        assert (
            "already exists" in data["message"].lower()
            or "duplicate" in data["message"].lower()
        )

    @pytest.mark.asyncio
    async def test_register_invalid_email(
        self, client: AsyncClient, sample_user_data: dict
    ):
        """Test registration with invalid email."""
        data = {**sample_user_data, "email": "invalid-email"}

        response = await client.post("/api/v1/auth/register", json=data)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_register_weak_password(
        self, client: AsyncClient, sample_user_data: dict
    ):
        """Test registration with weak password."""
        data = {**sample_user_data, "password": "123"}

        response = await client.post("/api/v1/auth/register", json=data)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_login_success(self, client: AsyncClient, sample_user_data: dict):
        """Test successful login."""
        # First register
        register_response = await client.post(
            "/api/v1/auth/register", json=sample_user_data
        )
        assert register_response.status_code == 201, (
            f"Registration failed: {register_response.json()}"
        )

        # Then login
        login_data = {
            "email": sample_user_data["email"],
            "password": sample_user_data["password"],
        }
        response = await client.post("/api/v1/auth/login", json=login_data)

        assert response.status_code == 200, f"Login failed: {response.json()}"
        data = response.json()
        assert data["success"] is True
        assert "tokens" in data["data"]
        assert data["data"]["tokens"]["access_token"] is not None

    @pytest.mark.asyncio
    async def test_login_invalid_credentials(
        self, client: AsyncClient, sample_user_data: dict
    ):
        """Test login with invalid credentials."""
        # Register first
        await client.post("/api/v1/auth/register", json=sample_user_data)

        # Try to login with wrong password
        login_data = {
            "email": sample_user_data["email"],
            "password": "wrongpassword",
        }
        response = await client.post("/api/v1/auth/login", json=login_data)

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, client: AsyncClient):
        """Test login with nonexistent user."""
        login_data = {
            "email": "nonexistent@example.com",
            "password": "somepassword",
        }
        response = await client.post("/api/v1/auth/login", json=login_data)

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_token_success(
        self, client: AsyncClient, sample_user_data: dict
    ):
        """Test successful token refresh."""
        # Register and get tokens
        register_response = await client.post(
            "/api/v1/auth/register", json=sample_user_data
        )
        tokens = register_response.json()["data"]["tokens"]

        # Refresh token
        refresh_data = {"refresh_token": tokens["refresh_token"]}
        response = await client.post("/api/v1/auth/refresh", json=refresh_data)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "access_token" in data["data"]

    @pytest.mark.asyncio
    async def test_refresh_token_invalid(self, client: AsyncClient):
        """Test refresh with invalid token."""
        refresh_data = {"refresh_token": "invalid_token"}
        response = await client.post("/api/v1/auth/refresh", json=refresh_data)

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user(self, client: AsyncClient, sample_user_data: dict):
        """Test getting current user profile."""
        # Register and get tokens
        register_response = await client.post(
            "/api/v1/auth/register", json=sample_user_data
        )
        tokens = register_response.json()["data"]["tokens"]

        # Get current user
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        response = await client.get("/api/v1/auth/me", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["email"] == sample_user_data["email"]

    @pytest.mark.asyncio
    async def test_get_current_user_unauthorized(self, client: AsyncClient):
        """Test getting current user without token."""
        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self, client: AsyncClient):
        """Test getting current user with invalid token."""
        headers = {"Authorization": "Bearer invalid_token"}
        response = await client.get("/api/v1/auth/me", headers=headers)

        assert response.status_code == 401
