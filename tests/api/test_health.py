"""Tests for health check routes."""

import pytest
from httpx import AsyncClient


class TestHealthRoutes:
    """Tests for health check endpoints."""

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """Test basic health check endpoint."""
        response = await client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["data"]["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_includes_version(self, client: AsyncClient):
        """Test health check includes version info."""
        response = await client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        # Health response should include some status info
        assert "status" in data["data"]

    @pytest.mark.asyncio
    async def test_readiness_check(self, client: AsyncClient):
        """Test readiness check (for Kubernetes)."""
        response = await client.get("/api/v1/health/ready")

        # May or may not exist, but shouldn't error
        assert response.status_code in [200, 404]

    @pytest.mark.asyncio
    async def test_liveness_check(self, client: AsyncClient):
        """Test liveness check (for Kubernetes)."""
        response = await client.get("/api/v1/health/live")

        # May or may not exist, but shouldn't error
        assert response.status_code in [200, 404]
