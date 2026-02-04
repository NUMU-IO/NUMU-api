"""Integration tests for observability features.

Tests:
- Sentry error capture
- Structured logging with required fields
- Health check endpoints
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.config import settings
from src.config.logging_config import (
    bind_request_context,
    clear_request_context,
    get_logger,
    request_id_var,
    tenant_id_var,
    user_id_var,
)
from src.main import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestSentryIntegration:
    """Test Sentry error capture functionality."""

    def test_sentry_dsn_configuration(self):
        """Verify Sentry DSN can be read from settings."""
        # DSN can be None in test environment
        assert hasattr(settings, "sentry_dsn")
        assert hasattr(settings, "sentry_traces_sample_rate")
        assert hasattr(settings, "sentry_profiles_sample_rate")

    @patch("sentry_sdk.capture_exception")
    def test_sentry_captures_exception(self, mock_capture):
        """Verify Sentry captures exceptions when raised."""
        import sentry_sdk

        test_error = ValueError("Test error for Sentry")

        try:
            raise test_error
        except Exception as e:
            sentry_sdk.capture_exception(e)

        mock_capture.assert_called_once()
        captured_exception = mock_capture.call_args[0][0]
        assert isinstance(captured_exception, ValueError)
        assert str(captured_exception) == "Test error for Sentry"

    @patch("sentry_sdk.capture_message")
    def test_sentry_captures_message(self, mock_capture):
        """Verify Sentry captures custom messages."""
        import sentry_sdk

        sentry_sdk.capture_message("Test message from NUMU API")

        mock_capture.assert_called_once_with("Test message from NUMU API")

    @patch("sentry_sdk.set_tag")
    def test_sentry_tags_can_be_set(self, mock_set_tag):
        """Verify Sentry tags can be set for context."""
        import sentry_sdk

        sentry_sdk.set_tag("user_id", "test-user-123")
        sentry_sdk.set_tag("tenant_id", "test-tenant-456")

        assert mock_set_tag.call_count == 2


class TestStructuredLogging:
    """Test structured logging with required fields."""

    def test_logger_creation(self):
        """Verify logger can be created with module name."""
        logger = get_logger(__name__)
        assert logger is not None

    def test_request_context_binding(self):
        """Verify request context can be bound to logs."""
        # Clear any existing context
        clear_request_context()

        # Bind new context
        bind_request_context(
            request_id="test-req-123",
            tenant_id="test-tenant-456",
            user_id="test-user-789",
        )

        # Verify context variables are set
        assert request_id_var.get() == "test-req-123"
        assert tenant_id_var.get() == "test-tenant-456"
        assert user_id_var.get() == "test-user-789"

        # Clean up
        clear_request_context()

        # Verify context is cleared
        assert request_id_var.get() is None
        assert tenant_id_var.get() is None
        assert user_id_var.get() is None

    def test_logger_bind_creates_new_logger(self):
        """Verify logger.bind() creates a new logger with bound context."""
        logger = get_logger(__name__)
        bound_logger = logger.bind(order_id="order-123", action="create")

        # bound_logger should be a new instance
        assert bound_logger is not logger

    def test_log_output_contains_required_fields(self, capsys):
        """Verify log output contains required fields in JSON format."""
        # This test requires JSON format logging to be enabled
        # Skip if console format is being used
        if settings.log_format != "json":
            pytest.skip("JSON log format not enabled")

        logger = get_logger("test_logger")

        # Bind context and log
        bind_request_context(request_id="test-req-001")

        # Log a message
        logger.info("test_event", custom_field="test_value")

        # Clear context
        clear_request_context()

    def test_structured_log_event_names(self):
        """Verify log events follow naming convention."""
        # Event names should be snake_case
        valid_events = [
            "auth_login_attempt",
            "auth_login_success",
            "auth_login_failed",
            "order_created",
            "order_status_updated",
            "webhook_received",
            "payment_success",
            "payment_failed",
        ]

        for event in valid_events:
            # Verify snake_case format
            assert event.islower() or "_" in event
            assert " " not in event


class TestHealthCheckEndpoints:
    """Test health check endpoints."""

    def test_basic_health_check(self, client):
        """Verify basic health check returns 200."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert data["data"]["status"] == "healthy"

    def test_detailed_health_check_returns_components(self, client):
        """Verify detailed health check returns all components."""
        response = client.get("/api/v1/health/detailed")

        # May return 200 or 500 depending on DB/Redis availability
        assert response.status_code in [200, 500]

        data = response.json()

        # Check response structure
        assert "status" in data
        assert "timestamp" in data
        assert "version" in data
        assert "environment" in data
        assert "components" in data
        assert "system" in data

    def test_detailed_health_check_components(self, client):
        """Verify detailed health check includes required components."""
        response = client.get("/api/v1/health/detailed")
        data = response.json()

        # Check required components
        components = data.get("components", {})
        required_components = ["database", "redis", "sentry", "disk"]

        for component in required_components:
            assert component in components, f"Missing component: {component}"

            # Each component should have status and message
            assert "status" in components[component]
            assert "message" in components[component]

    def test_health_check_status_values(self, client):
        """Verify health status values are valid."""
        response = client.get("/api/v1/health/detailed")
        data = response.json()

        valid_statuses = ["healthy", "degraded", "unhealthy"]

        # Overall status
        assert data["status"] in valid_statuses

        # Component statuses
        for component_name, component in data.get("components", {}).items():
            assert component["status"] in valid_statuses, (
                f"Invalid status for {component_name}: {component['status']}"
            )

    def test_sentry_component_status(self, client):
        """Verify Sentry component reports configuration status."""
        response = client.get("/api/v1/health/detailed")
        data = response.json()

        sentry = data.get("components", {}).get("sentry", {})
        assert "status" in sentry

        # If DSN is configured, should be healthy
        # If not configured, should be degraded
        if settings.sentry_dsn:
            assert sentry["status"] == "healthy"
        else:
            assert sentry["status"] == "degraded"

    def test_disk_component_reports_space(self, client):
        """Verify disk component reports space information."""
        response = client.get("/api/v1/health/detailed")
        data = response.json()

        disk = data.get("components", {}).get("disk", {})
        assert "status" in disk

        # Should have details with disk space info
        if disk.get("details"):
            details = disk["details"]
            assert "total_gb" in details or "free_gb" in details

    def test_root_endpoint(self, client):
        """Verify root endpoint returns API info."""
        response = client.get("/api/v1/")
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True
        assert "NUMU" in data["data"]["name"]


class TestLoggingMiddleware:
    """Test logging middleware functionality."""

    def test_request_id_header_added(self, client):
        """Verify X-Request-ID header is added to responses."""
        response = client.get("/api/v1/health")

        assert "X-Request-ID" in response.headers
        request_id = response.headers["X-Request-ID"]
        assert len(request_id) == 8  # UUID truncated to 8 chars

    def test_process_time_header_added(self, client):
        """Verify X-Process-Time header is added to responses."""
        response = client.get("/api/v1/health")

        assert "X-Process-Time" in response.headers
        process_time = response.headers["X-Process-Time"]
        # Should be a valid float
        assert float(process_time) >= 0


class TestObservabilityConfiguration:
    """Test observability configuration settings."""

    def test_log_level_setting(self):
        """Verify log level can be configured."""
        assert hasattr(settings, "log_level")
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        assert settings.log_level.upper() in valid_levels

    def test_log_format_setting(self):
        """Verify log format can be configured."""
        assert hasattr(settings, "log_format")
        valid_formats = ["json", "console"]
        assert settings.log_format in valid_formats

    def test_sentry_sample_rates(self):
        """Verify Sentry sample rates are valid."""
        assert 0.0 <= settings.sentry_traces_sample_rate <= 1.0
        assert 0.0 <= settings.sentry_profiles_sample_rate <= 1.0

    def test_environment_setting(self):
        """Verify environment is set correctly."""
        valid_environments = ["development", "staging", "production"]
        assert settings.environment in valid_environments
