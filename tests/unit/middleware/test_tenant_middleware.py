"""Unit tests for tenant middleware."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.middleware.tenant_middleware import TenantMiddleware


class TestTenantMiddleware:
    """Tests for TenantMiddleware."""

    def setup_method(self):
        """Set up test fixtures."""
        self.middleware = TenantMiddleware(app=MagicMock())

    def test_extract_subdomain_with_valid_subdomain(self):
        """Test subdomain extraction with valid subdomain."""
        assert self.middleware._extract_subdomain("store1.octyrafiy.com") == "store1"
        assert self.middleware._extract_subdomain("mystore.localhost") == "mystore"
        assert self.middleware._extract_subdomain("test.example.com") == "test"

    def test_extract_subdomain_no_subdomain(self):
        """Test subdomain extraction without subdomain."""
        assert self.middleware._extract_subdomain("localhost") is None
        # Two-part domain is treated as having a subdomain (first part)
        # Only single-part domains return None

    def test_extract_subdomain_skips_common_subdomains(self):
        """Test that www, api, admin subdomains are skipped."""
        assert self.middleware._extract_subdomain("www.octyrafiy.com") is None
        assert self.middleware._extract_subdomain("api.octyrafiy.com") is None
        assert self.middleware._extract_subdomain("admin.octyrafiy.com") is None

    def test_extract_subdomain_deep_subdomain(self):
        """Test extraction with multiple subdomain levels."""
        # Only extracts first subdomain
        assert self.middleware._extract_subdomain("store1.api.example.com") == "store1"

    @pytest.mark.asyncio
    async def test_dispatch_public_path_skips_tenant_lookup(self):
        """Test that public paths skip tenant lookup."""
        mock_request = MagicMock()
        mock_request.url.path = "/health"
        mock_request.state = MagicMock()

        mock_call_next = AsyncMock(return_value=MagicMock())

        await self.middleware.dispatch(mock_request, mock_call_next)

        assert mock_request.state.tenant is None
        mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_dispatch_docs_path_skips_tenant_lookup(self):
        """Test that docs paths skip tenant lookup."""
        mock_request = MagicMock()
        mock_request.url.path = "/docs"
        mock_request.state = MagicMock()

        mock_call_next = AsyncMock(return_value=MagicMock())

        await self.middleware.dispatch(mock_request, mock_call_next)

        assert mock_request.state.tenant is None
        mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_dispatch_auth_path_skips_tenant_lookup(self):
        """Test that auth paths skip tenant lookup."""
        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/auth/login"
        mock_request.state = MagicMock()

        mock_call_next = AsyncMock(return_value=MagicMock())

        await self.middleware.dispatch(mock_request, mock_call_next)

        assert mock_request.state.tenant is None
        mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_dispatch_no_subdomain_skips_tenant_lookup(self):
        """Test that requests without subdomain skip tenant lookup."""
        mock_request = MagicMock()
        mock_request.url.path = "/api/v1/products"
        mock_request.headers.get.return_value = "localhost:8000"
        mock_request.state = MagicMock()

        mock_call_next = AsyncMock(return_value=MagicMock())

        await self.middleware.dispatch(mock_request, mock_call_next)

        assert mock_request.state.tenant is None
        mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    @patch("src.api.middleware.tenant_middleware.reset_tenant_context")
    @patch("src.api.middleware.tenant_middleware.set_tenant_schema")
    @patch("src.api.middleware.tenant_middleware.AsyncSessionLocal")
    async def test_dispatch_with_valid_tenant(
        self, mock_session_local, mock_set_schema, mock_reset_context
    ):
        """Test dispatch with valid tenant subdomain."""

        # Setup mock tenant
        mock_tenant = MagicMock()
        mock_tenant.is_active = True
        mock_tenant.schema_name = "tenant_store1"

        # Setup mock session
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_local.return_value = mock_session

        # Setup mock repository
        with patch(
            "src.api.middleware.tenant_middleware.TenantRepository"
        ) as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.get_by_subdomain = AsyncMock(return_value=mock_tenant)
            mock_repo_class.return_value = mock_repo

            mock_request = MagicMock()
            mock_request.url.path = "/api/v1/products"
            mock_request.headers.get.return_value = "store1.octyrafiy.com"
            mock_request.state = MagicMock()

            mock_response = MagicMock()
            mock_call_next = AsyncMock(return_value=mock_response)

            await self.middleware.dispatch(mock_request, mock_call_next)

            assert mock_request.state.tenant == mock_tenant
            mock_set_schema.assert_called_once_with("tenant_store1")
            mock_reset_context.assert_called_once()
            mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    @patch("src.api.middleware.tenant_middleware.reset_tenant_context")
    @patch("src.api.middleware.tenant_middleware.AsyncSessionLocal")
    async def test_dispatch_with_inactive_tenant_raises_404(
        self, mock_session_local, mock_reset_context
    ):
        """Test dispatch with inactive tenant raises 404."""
        # Setup mock tenant
        mock_tenant = MagicMock()
        mock_tenant.is_active = False

        # Setup mock session
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_local.return_value = mock_session

        # Setup mock repository
        with patch(
            "src.api.middleware.tenant_middleware.TenantRepository"
        ) as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.get_by_subdomain = AsyncMock(return_value=mock_tenant)
            mock_repo_class.return_value = mock_repo

            mock_request = MagicMock()
            mock_request.url.path = "/api/v1/products"
            mock_request.headers.get.return_value = "inactive.octyrafiy.com"
            mock_request.state = MagicMock()

            mock_call_next = AsyncMock()

            with pytest.raises(HTTPException) as exc_info:
                await self.middleware.dispatch(mock_request, mock_call_next)

            assert exc_info.value.status_code == 404
            assert "inactive" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    @patch("src.api.middleware.tenant_middleware.reset_tenant_context")
    @patch("src.api.middleware.tenant_middleware.AsyncSessionLocal")
    async def test_dispatch_with_nonexistent_tenant_raises_404(
        self, mock_session_local, mock_reset_context
    ):
        """Test dispatch with nonexistent tenant raises 404."""
        # Setup mock session
        mock_session = MagicMock()
        mock_session.execute = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_local.return_value = mock_session

        # Setup mock repository returning None
        with patch(
            "src.api.middleware.tenant_middleware.TenantRepository"
        ) as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.get_by_subdomain = AsyncMock(return_value=None)
            mock_repo_class.return_value = mock_repo

            mock_request = MagicMock()
            mock_request.url.path = "/api/v1/products"
            mock_request.headers.get.return_value = "nonexistent.octyrafiy.com"
            mock_request.state = MagicMock()

            mock_call_next = AsyncMock()

            with pytest.raises(HTTPException) as exc_info:
                await self.middleware.dispatch(mock_request, mock_call_next)

            assert exc_info.value.status_code == 404
