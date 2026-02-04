"""Unit tests for Slack alerting system.

Tests:
- Alert creation and validation
- Channel routing logic
- Block Kit formatting
- Rate limiting/deduplication
- Alert service functionality
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.slack.alerts import (
    AlertAction,
    AlertService,
    AlertSeverity,
    SlackAlert,
    create_critical_alert,
    create_fraud_alert,
    create_infrastructure_alert,
    create_payment_alert,
    create_shipping_alert,
    create_warn_alert,
)
from src.infrastructure.slack.channels import (
    AlertChannel,
    get_all_channels_for_alert,
    get_channel_for_alert,
    should_escalate_to_critical,
)
from src.infrastructure.slack.formatters import (
    build_fields_block,
    build_header_block,
    format_alert_to_blocks,
    format_amount,
)


class TestAlertSeverity:
    """Test AlertSeverity enum."""

    def test_severity_values(self):
        """Verify severity level values."""
        assert AlertSeverity.CRITICAL.value == "CRITICAL"
        assert AlertSeverity.WARN.value == "WARN"
        assert AlertSeverity.INFO.value == "INFO"


class TestSlackAlert:
    """Test SlackAlert dataclass."""

    def test_basic_alert_creation(self):
        """Test creating a basic alert."""
        alert = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Test alert",
            service=AlertService.PAYMENTS,
        )

        assert alert.severity == AlertSeverity.WARN
        assert alert.title == "Test alert"
        assert alert.service == AlertService.PAYMENTS
        assert alert.correlation_id is not None
        assert len(alert.correlation_id) == 12

    def test_alert_title_truncation(self):
        """Test that long titles are truncated."""
        long_title = "A" * 150
        alert = SlackAlert(
            severity=AlertSeverity.INFO,
            title=long_title,
            service=AlertService.BUSINESS,
        )

        assert len(alert.title) == 100
        assert alert.title.endswith("...")

    def test_alert_dedup_key(self):
        """Test deduplication key generation."""
        alert1 = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Same title",
            service=AlertService.PAYMENTS,
            tenant_id="tenant_123",
        )
        alert2 = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Same title",
            service=AlertService.PAYMENTS,
            tenant_id="tenant_123",
        )
        alert3 = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Different title",
            service=AlertService.PAYMENTS,
            tenant_id="tenant_123",
        )

        # Same alert type should have same dedup key
        assert alert1.dedup_key == alert2.dedup_key
        # Different title should have different dedup key
        assert alert1.dedup_key != alert3.dedup_key

    def test_severity_emoji(self):
        """Test severity emoji mapping."""
        critical = SlackAlert(
            severity=AlertSeverity.CRITICAL,
            title="Test",
            service=AlertService.PAYMENTS,
        )
        warn = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Test",
            service=AlertService.PAYMENTS,
        )
        info = SlackAlert(
            severity=AlertSeverity.INFO,
            title="Test",
            service=AlertService.PAYMENTS,
        )

        assert critical.severity_emoji == "\U0001F534"  # Red
        assert warn.severity_emoji == "\U0001F7E1"      # Yellow
        assert info.severity_emoji == "\U0001F535"      # Blue

    def test_with_suppressed_count(self):
        """Test adding suppressed count to alert."""
        alert = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Original title",
            service=AlertService.PAYMENTS,
        )

        new_alert = alert.with_suppressed_count(5)

        assert "(+5 similar)" in new_alert.title
        assert alert.title == "Original title"  # Original unchanged

    def test_string_severity_conversion(self):
        """Test that string severity is converted to enum."""
        alert = SlackAlert(
            severity="CRITICAL",  # type: ignore
            title="Test",
            service="payments",  # type: ignore
        )

        assert alert.severity == AlertSeverity.CRITICAL
        assert alert.service == AlertService.PAYMENTS


class TestAlertBuilders:
    """Test alert builder functions."""

    def test_create_critical_alert(self):
        """Test creating critical alert."""
        alert = create_critical_alert(
            title="System down",
            service=AlertService.INFRASTRUCTURE,
            tenant_id="tenant_123",
        )

        assert alert.severity == AlertSeverity.CRITICAL
        assert alert.title == "System down"
        assert alert.tenant_id == "tenant_123"

    def test_create_warn_alert(self):
        """Test creating warning alert."""
        alert = create_warn_alert(
            title="High latency",
            service="infra",
        )

        assert alert.severity == AlertSeverity.WARN
        assert alert.service == AlertService.INFRASTRUCTURE

    def test_create_payment_alert(self):
        """Test creating payment alert."""
        alert = create_payment_alert(
            title="Gateway timeout",
            severity=AlertSeverity.WARN,
            gateway="paymob",
            amount=Decimal("100.50"),
            order_id="ORD-123",
        )

        assert "[PAYMENTS]" in alert.title
        assert alert.service == AlertService.PAYMENTS
        assert alert.amount == Decimal("100.50")
        assert alert.details["gateway"] == "paymob"

    def test_create_fraud_alert(self):
        """Test creating fraud alert."""
        alert = create_fraud_alert(
            title="High-risk order",
            severity=AlertSeverity.WARN,
            fraud_score=0.87,
            risk_signals=["Multiple addresses", "New customer"],
            order_id="ORD-456",
            tenant_id="tenant_abc",
        )

        assert "[FRAUD]" in alert.title
        assert alert.service == AlertService.FRAUD
        assert alert.details["fraud_score"] == "0.87"
        assert len(alert.risk_signals) == 2

    def test_create_shipping_alert(self):
        """Test creating shipping alert."""
        alert = create_shipping_alert(
            title="AWB creation failed",
            severity=AlertSeverity.WARN,
            awb="AWB123456",
            carrier="bosta",
        )

        assert "[SHIPPING]" in alert.title
        assert alert.details["awb"] == "AWB123456"
        assert alert.details["carrier"] == "bosta"

    def test_create_infrastructure_alert(self):
        """Test creating infrastructure alert."""
        alert = create_infrastructure_alert(
            title="Disk space low",
            severity=AlertSeverity.WARN,
            component="disk",
            metric_value=15.5,
            threshold=20.0,
            runbook_url="https://runbook.example.com/disk",
        )

        assert "[INFRA]" in alert.title
        assert alert.details["component"] == "disk"
        assert alert.details["metric_value"] == 15.5
        assert alert.runbook_url == "https://runbook.example.com/disk"


class TestChannelRouting:
    """Test channel routing logic."""

    def test_payment_alert_routing(self):
        """Test payment alerts go to payments channel."""
        alert = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Test",
            service=AlertService.PAYMENTS,
        )

        channel = get_channel_for_alert(alert)
        assert channel == AlertChannel.PAYMENTS

    def test_fraud_alert_routing(self):
        """Test fraud alerts go to fraud channel."""
        alert = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Test",
            service=AlertService.FRAUD,
        )

        channel = get_channel_for_alert(alert)
        assert channel == AlertChannel.FRAUD

    def test_security_critical_routing(self):
        """Test security CRITICAL alerts go to critical channel."""
        alert = SlackAlert(
            severity=AlertSeverity.CRITICAL,
            title="Test",
            service=AlertService.SECURITY,
        )

        channel = get_channel_for_alert(alert)
        assert channel == AlertChannel.CRITICAL

    def test_escalation_for_critical_payments(self):
        """Test CRITICAL payment alerts escalate to critical channel."""
        alert = SlackAlert(
            severity=AlertSeverity.CRITICAL,
            title="Test",
            service=AlertService.PAYMENTS,
        )

        assert should_escalate_to_critical(alert) is True

    def test_no_escalation_for_warn(self):
        """Test WARN alerts don't escalate."""
        alert = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Test",
            service=AlertService.PAYMENTS,
        )

        assert should_escalate_to_critical(alert) is False

    def test_get_all_channels_with_escalation(self):
        """Test getting all channels including escalation."""
        alert = SlackAlert(
            severity=AlertSeverity.CRITICAL,
            title="Test",
            service=AlertService.FRAUD,
        )

        channels = get_all_channels_for_alert(alert)

        assert AlertChannel.FRAUD in channels
        assert AlertChannel.CRITICAL in channels


class TestFormatters:
    """Test Block Kit formatters."""

    def test_format_amount_egp(self):
        """Test EGP amount formatting."""
        assert format_amount(1234.56, "EGP") == "EGP 1,234.56"
        assert format_amount(100, "EGP") == "EGP 100.00"

    def test_format_amount_none(self):
        """Test None amount formatting."""
        assert format_amount(None) == "N/A"

    def test_build_header_block(self):
        """Test header block generation."""
        alert = SlackAlert(
            severity=AlertSeverity.CRITICAL,
            title="Test",
            service=AlertService.PAYMENTS,
        )

        header = build_header_block(alert)

        assert header["type"] == "header"
        assert "CRITICAL" in header["text"]["text"]
        assert "PAYMENTS" in header["text"]["text"]

    def test_build_fields_block(self):
        """Test fields block generation."""
        alert = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Test",
            service=AlertService.PAYMENTS,
            tenant_id="tenant_123",
            order_id="ORD-456",
            amount=Decimal("500.00"),
        )

        fields = build_fields_block(alert)

        assert fields is not None
        assert fields["type"] == "section"
        assert len(fields["fields"]) >= 3

    def test_format_alert_to_blocks(self):
        """Test full alert formatting."""
        alert = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Test payment alert",
            service=AlertService.PAYMENTS,
            tenant_id="tenant_123",
            correlation_id="corr123",
        )

        payload = format_alert_to_blocks(alert)

        assert "blocks" in payload
        assert "text" in payload  # Fallback text
        assert len(payload["blocks"]) >= 3  # Header, title, context at minimum

    def test_format_fraud_alert_includes_risk_signals(self):
        """Test fraud alert formatting includes risk signals."""
        alert = create_fraud_alert(
            title="High-risk order",
            severity=AlertSeverity.WARN,
            fraud_score=0.85,
            risk_signals=["Signal 1", "Signal 2"],
            order_id="ORD-123",
            tenant_id="tenant_abc",
        )

        payload = format_alert_to_blocks(alert)

        # Find risk signals block
        blocks_text = str(payload["blocks"])
        assert "Signal 1" in blocks_text or "Risk Signals" in blocks_text


class TestAlertActions:
    """Test alert action buttons."""

    def test_alert_with_actions(self):
        """Test alert with action buttons."""
        alert = SlackAlert(
            severity=AlertSeverity.WARN,
            title="Test",
            service=AlertService.FRAUD,
            actions=[
                AlertAction(text="Approve", action_id="approve", style="primary"),
                AlertAction(text="Reject", action_id="reject", style="danger"),
            ],
            sentry_url="https://sentry.io/issue/123",
        )

        payload = format_alert_to_blocks(alert)
        blocks_str = str(payload["blocks"])

        assert "Approve" in blocks_str
        assert "Sentry" in blocks_str


class TestSlackAlertService:
    """Test SlackAlertService class."""

    @pytest.fixture
    def mock_client(self):
        """Create mock Slack client."""
        client = MagicMock()
        client.post_webhook = AsyncMock(return_value=True)
        return client

    @pytest.fixture
    def mock_rate_limiter(self):
        """Create mock rate limiter."""
        limiter = MagicMock()
        limiter.should_send = AsyncMock(return_value=(True, 0))
        return limiter

    @pytest.mark.asyncio
    async def test_send_alert_when_disabled(self, mock_client, mock_rate_limiter):
        """Test that alerts are skipped when Slack is disabled."""
        from src.infrastructure.slack.service import SlackAlertService

        with patch("src.infrastructure.slack.service.settings") as mock_settings:
            mock_settings.slack_enabled = False

            service = SlackAlertService(
                client=mock_client,
                rate_limiter=mock_rate_limiter,
            )

            alert = SlackAlert(
                severity=AlertSeverity.WARN,
                title="Test",
                service=AlertService.PAYMENTS,
            )

            result = await service.send_alert(alert)

            assert result is False
            mock_client.post_webhook.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_alert_rate_limited(self, mock_client, mock_rate_limiter):
        """Test that rate-limited alerts are suppressed."""
        from src.infrastructure.slack.service import SlackAlertService

        mock_rate_limiter.should_send = AsyncMock(return_value=(False, 5))

        with patch("src.infrastructure.slack.service.settings") as mock_settings:
            mock_settings.slack_enabled = True

            service = SlackAlertService(
                client=mock_client,
                rate_limiter=mock_rate_limiter,
            )

            alert = SlackAlert(
                severity=AlertSeverity.WARN,
                title="Test",
                service=AlertService.PAYMENTS,
            )

            result = await service.send_alert(alert)

            assert result is True  # Suppressed counts as handled
            mock_client.post_webhook.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_alert_success(self, mock_client, mock_rate_limiter):
        """Test successful alert sending."""
        from src.infrastructure.slack.service import SlackAlertService

        with patch("src.infrastructure.slack.service.settings") as mock_settings:
            mock_settings.slack_enabled = True
            mock_settings.slack_user_oncall = None
            mock_settings.slack_user_fraud_lead = None
            mock_settings.environment = "development"

            service = SlackAlertService(
                client=mock_client,
                rate_limiter=mock_rate_limiter,
            )

            alert = SlackAlert(
                severity=AlertSeverity.WARN,
                title="Test",
                service=AlertService.PAYMENTS,
            )

            result = await service.send_alert(alert)

            assert result is True
            mock_client.post_webhook.assert_called()

    @pytest.mark.asyncio
    async def test_convenience_methods(self, mock_client, mock_rate_limiter):
        """Test convenience alert methods."""
        from src.infrastructure.slack.service import SlackAlertService

        with patch("src.infrastructure.slack.service.settings") as mock_settings:
            mock_settings.slack_enabled = True
            mock_settings.slack_user_oncall = None
            mock_settings.slack_user_fraud_lead = None
            mock_settings.environment = "development"

            service = SlackAlertService(
                client=mock_client,
                rate_limiter=mock_rate_limiter,
            )

            # Test payment failure
            await service.alert_payment_failure(
                title="Gateway down",
                gateway="paymob",
                critical=True,
            )
            assert mock_client.post_webhook.called

            mock_client.post_webhook.reset_mock()

            # Test fraud detected
            await service.alert_fraud_detected(
                order_id="ORD-123",
                tenant_id="tenant_abc",
                fraud_score=0.9,
                amount=1000.0,
                risk_signals=["Test signal"],
            )
            assert mock_client.post_webhook.called
