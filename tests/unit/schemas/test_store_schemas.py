"""Unit tests for store schemas."""

import pytest
from pydantic import ValidationError

from src.api.v1.schemas.tenant.store import (
    CreateStoreRequest,
    UpdateStoreRequest,
    StoreResponse,
)


class TestCreateStoreRequest:
    """Tests for CreateStoreRequest schema."""

    def test_valid_minimal_store(self):
        """Test creating store with minimal required fields."""
        data = {
            "name": "My Store",
            "subdomain": "mystore",
        }
        request = CreateStoreRequest(**data)

        assert request.name == "My Store"
        assert request.subdomain == "mystore"
        assert request.slug is None
        assert request.description is None
        assert request.default_currency == "EGP"  # Default

    def test_valid_full_store(self):
        """Test creating store with all fields."""
        data = {
            "name": "Full Store",
            "subdomain": "fullstore",
            "slug": "full-store",
            "description": "A fully configured store",
            "default_currency": "EGP",
            "contact_email": "store@example.com",
            "contact_phone": "+201234567890",
        }
        request = CreateStoreRequest(**data)

        assert request.name == "Full Store"
        assert request.subdomain == "fullstore"
        assert request.slug == "full-store"
        assert request.description == "A fully configured store"
        assert request.default_currency == "EGP"
        assert request.contact_email == "store@example.com"
        assert request.contact_phone == "+201234567890"

    def test_empty_name_fails(self):
        """Test validation fails for empty name."""
        data = {
            "name": "",
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateStoreRequest(**data)

        assert "name" in str(exc_info.value).lower()

    def test_name_too_long(self):
        """Test validation fails for name exceeding max length."""
        data = {
            "name": "A" * 256,  # Exceeds 255 char limit
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateStoreRequest(**data)

        assert "name" in str(exc_info.value).lower()

    def test_invalid_contact_email(self):
        """Test validation fails for invalid contact email."""
        data = {
            "name": "My Store",
            "contact_email": "not-an-email",
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateStoreRequest(**data)

        assert "email" in str(exc_info.value).lower()

    def test_currency_max_length(self):
        """Test validation for currency max length."""
        data = {
            "name": "My Store",
            "default_currency": "USDT",  # Exceeds 3 char limit
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateStoreRequest(**data)

        assert "currency" in str(exc_info.value).lower()

    def test_phone_max_length(self):
        """Test validation for phone max length."""
        data = {
            "name": "My Store",
            "contact_phone": "1" * 21,  # Exceeds 20 char limit
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateStoreRequest(**data)

        assert "phone" in str(exc_info.value).lower()

    def test_slug_max_length(self):
        """Test validation for slug max length."""
        data = {
            "name": "My Store",
            "slug": "a" * 256,  # Exceeds 255 char limit
        }
        with pytest.raises(ValidationError) as exc_info:
            CreateStoreRequest(**data)

        assert "slug" in str(exc_info.value).lower()


class TestUpdateStoreRequest:
    """Tests for UpdateStoreRequest schema."""

    def test_valid_partial_update(self):
        """Test partial update with only some fields."""
        data = {
            "name": "Updated Store Name",
        }
        request = UpdateStoreRequest(**data)

        assert request.name == "Updated Store Name"
        assert request.description is None
        assert request.logo_url is None

    def test_valid_empty_update(self):
        """Test update with no fields (all optional)."""
        request = UpdateStoreRequest()

        assert request.name is None
        assert request.description is None

    def test_valid_full_update(self):
        """Test update with all fields."""
        data = {
            "name": "Updated Store",
            "description": "Updated description",
            "logo_url": "https://example.com/logo.png",
            "banner_url": "https://example.com/banner.png",
            "contact_email": "new@example.com",
            "contact_phone": "+201111111111",
            "address": {"city": "Cairo", "country": "Egypt"},
            "social_links": {"facebook": "https://facebook.com/store"},
            "settings": {"theme": "dark"},
        }
        request = UpdateStoreRequest(**data)

        assert request.name == "Updated Store"
        assert request.logo_url == "https://example.com/logo.png"
        assert request.address == {"city": "Cairo", "country": "Egypt"}
        assert request.social_links == {"facebook": "https://facebook.com/store"}
        assert request.settings == {"theme": "dark"}

    def test_empty_name_fails(self):
        """Test validation fails for empty name when provided."""
        data = {
            "name": "",
        }
        with pytest.raises(ValidationError) as exc_info:
            UpdateStoreRequest(**data)

        assert "name" in str(exc_info.value).lower()

    def test_invalid_contact_email(self):
        """Test validation fails for invalid contact email."""
        data = {
            "contact_email": "not-an-email",
        }
        with pytest.raises(ValidationError):
            UpdateStoreRequest(**data)

    def test_logo_url_max_length(self):
        """Test validation for logo URL max length."""
        data = {
            "logo_url": "https://example.com/" + "a" * 500,
        }
        with pytest.raises(ValidationError) as exc_info:
            UpdateStoreRequest(**data)

        assert "logo" in str(exc_info.value).lower()

    def test_banner_url_max_length(self):
        """Test validation for banner URL max length."""
        data = {
            "banner_url": "https://example.com/" + "a" * 500,
        }
        with pytest.raises(ValidationError) as exc_info:
            UpdateStoreRequest(**data)

        assert "banner" in str(exc_info.value).lower()


class TestStoreResponse:
    """Tests for StoreResponse schema."""

    def test_valid_response(self):
        """Test valid store response."""
        data = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "name": "Test Store",
            "slug": "test-store",
            "subdomain": "teststore",
            "custom_domain": None,
            "store_url": "https://teststore.numu.io",
            "owner_id": "123e4567-e89b-12d3-a456-426614174001",
            "description": "A test store",
            "logo_url": "https://example.com/logo.png",
            "banner_url": "https://example.com/banner.png",
            "status": "active",
            "default_currency": "EGP",
            "contact_email": "store@example.com",
            "contact_phone": "+201234567890",
            "address": {"city": "Cairo", "country": "Egypt"},
            "social_links": {"facebook": "https://facebook.com/store"},
            "theme_settings": {"primaryColor": "#0075FF"},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        response = StoreResponse(**data)

        assert response.name == "Test Store"
        assert response.slug == "test-store"
        assert response.status == "active"
        assert response.default_currency == "EGP"

    def test_response_with_null_optional_fields(self):
        """Test store response with null optional fields."""
        data = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "name": "Test Store",
            "slug": "test-store",
            "subdomain": None,
            "custom_domain": None,
            "store_url": "https://test-store.numu.io",
            "owner_id": "123e4567-e89b-12d3-a456-426614174001",
            "description": None,
            "logo_url": None,
            "banner_url": None,
            "status": "active",
            "default_currency": "USD",
            "contact_email": None,
            "contact_phone": None,
            "address": {},
            "social_links": {},
            "theme_settings": {},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }
        response = StoreResponse(**data)

        assert response.description is None
        assert response.logo_url is None
        assert response.banner_url is None
        assert response.contact_email is None
        assert response.contact_phone is None

    def test_response_various_statuses(self):
        """Test store response with various status values."""
        base_data = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "name": "Test Store",
            "slug": "test-store",
            "subdomain": "teststore",
            "custom_domain": None,
            "store_url": "https://teststore.numu.io",
            "owner_id": "123e4567-e89b-12d3-a456-426614174001",
            "description": None,
            "logo_url": None,
            "banner_url": None,
            "default_currency": "USD",
            "contact_email": None,
            "contact_phone": None,
            "address": {},
            "social_links": {},
            "theme_settings": {},
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }

        for status in ["active", "inactive", "suspended"]:
            data = {**base_data, "status": status}
            response = StoreResponse(**data)
            assert response.status == status
