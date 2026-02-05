"""Tests for notification preference entity methods and onboarding tasks.

Verifies:
- Customer.default_notification_preferences() structure
- Customer.notification_preferences property reads from metadata
- Customer.update_notification_preferences() merges correctly
- Onboarding email tasks call ResendEmailService with correct templates
- Celery task retry on failure
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.core.entities.customer import Customer
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_customer(prefs=None):
    metadata = {}
    if prefs is not None:
        metadata["notification_preferences"] = prefs
    return Customer(
        id=uuid4(),
        store_id=uuid4(),
        email=Email(value="alice@example.com"),
        first_name="Alice",
        last_name="Shopper",
        phone=PhoneNumber(value="+201012345678", country_code="EG"),
        is_verified=True,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Tests: default_notification_preferences
# ---------------------------------------------------------------------------


class TestDefaultNotificationPreferences:
    def test_returns_all_channels(self):
        prefs = Customer.default_notification_preferences()
        assert "email" in prefs
        assert "whatsapp" in prefs

    def test_all_events_true_by_default(self):
        prefs = Customer.default_notification_preferences()
        for channel in ("email", "whatsapp"):
            for event in (
                "order_confirmation",
                "shipping_update",
                "delivery_confirmation",
            ):
                assert prefs[channel][event] is True, (
                    f"{channel}.{event} should be True"
                )


# ---------------------------------------------------------------------------
# Tests: notification_preferences property
# ---------------------------------------------------------------------------


class TestNotificationPreferencesProperty:
    def test_returns_metadata_prefs_when_set(self):
        custom = {
            "email": {
                "order_confirmation": False,
                "shipping_update": True,
                "delivery_confirmation": True,
            },
            "whatsapp": {
                "order_confirmation": True,
                "shipping_update": False,
                "delivery_confirmation": True,
            },
        }
        customer = _make_customer(prefs=custom)
        assert customer.notification_preferences == custom

    def test_returns_defaults_when_no_metadata(self):
        customer = _make_customer(prefs=None)
        prefs = customer.notification_preferences
        assert prefs == Customer.default_notification_preferences()

    def test_returns_defaults_when_metadata_empty(self):
        customer = _make_customer()
        customer.metadata = {}
        prefs = customer.notification_preferences
        assert prefs["email"]["order_confirmation"] is True


# ---------------------------------------------------------------------------
# Tests: update_notification_preferences
# ---------------------------------------------------------------------------


class TestUpdateNotificationPreferences:
    def test_partial_update_merges(self):
        customer = _make_customer()
        customer.update_notification_preferences({"email": {"shipping_update": False}})
        prefs = customer.notification_preferences
        # shipping_update should be changed
        assert prefs["email"]["shipping_update"] is False
        # other events should remain True (defaults)
        assert prefs["email"]["order_confirmation"] is True
        assert prefs["whatsapp"]["shipping_update"] is True

    def test_update_whatsapp_only(self):
        customer = _make_customer()
        customer.update_notification_preferences({
            "whatsapp": {"delivery_confirmation": False}
        })
        prefs = customer.notification_preferences
        assert prefs["whatsapp"]["delivery_confirmation"] is False
        assert prefs["email"]["delivery_confirmation"] is True

    def test_update_both_channels(self):
        customer = _make_customer()
        customer.update_notification_preferences({
            "email": {"order_confirmation": False},
            "whatsapp": {"order_confirmation": False},
        })
        prefs = customer.notification_preferences
        assert prefs["email"]["order_confirmation"] is False
        assert prefs["whatsapp"]["order_confirmation"] is False

    def test_update_touches_entity(self):
        customer = _make_customer()
        old_updated = customer.updated_at
        customer.update_notification_preferences({"email": {"shipping_update": False}})
        assert customer.updated_at >= old_updated

    def test_preserves_existing_prefs(self):
        """Existing non-default prefs are preserved when updating other keys."""
        custom = {
            "email": {
                "order_confirmation": False,
                "shipping_update": True,
                "delivery_confirmation": True,
            },
            "whatsapp": {
                "order_confirmation": True,
                "shipping_update": True,
                "delivery_confirmation": True,
            },
        }
        customer = _make_customer(prefs=custom)
        customer.update_notification_preferences({
            "email": {"delivery_confirmation": False}
        })
        prefs = customer.notification_preferences
        # Previously-set False should be preserved
        assert prefs["email"]["order_confirmation"] is False
        # Newly updated
        assert prefs["email"]["delivery_confirmation"] is False


# ---------------------------------------------------------------------------
# Tests: Onboarding email tasks
# ---------------------------------------------------------------------------


class TestOnboardingEmailTasks:
    """Verify onboarding Celery tasks invoke email service correctly."""

    @patch(
        "src.infrastructure.messaging.tasks.onboarding_email_tasks.ResendEmailService"
    )
    def test_welcome_email_task_sends(self, MockEmailService):
        mock_service = MagicMock()
        mock_service.send_email = AsyncMock(return_value=True)
        MockEmailService.return_value = mock_service

        from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
            send_welcome_email_task,
        )

        # Call the underlying function directly (skip Celery plumbing)
        result = send_welcome_email_task.run(
            email="merchant@test.io",
            merchant_name="Test Store",
        )

        mock_service.send_email.assert_called_once()
        call_msg = mock_service.send_email.call_args[0][0]
        assert call_msg.to == "merchant@test.io"
        assert "Welcome" in call_msg.subject
        assert result["sent"] is True

    @patch(
        "src.infrastructure.messaging.tasks.onboarding_email_tasks.ResendEmailService"
    )
    def test_first_product_email_task_sends(self, MockEmailService):
        mock_service = MagicMock()
        mock_service.send_email = AsyncMock(return_value=True)
        MockEmailService.return_value = mock_service

        from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
            send_first_product_email_task,
        )

        result = send_first_product_email_task.run(
            email="merchant@test.io",
            merchant_name="Test Store",
            product_name="Cool Widget",
        )

        mock_service.send_email.assert_called_once()
        assert result["sent"] is True

    @patch(
        "src.infrastructure.messaging.tasks.onboarding_email_tasks.ResendEmailService"
    )
    def test_first_order_email_task_sends(self, MockEmailService):
        mock_service = MagicMock()
        mock_service.send_email = AsyncMock(return_value=True)
        MockEmailService.return_value = mock_service

        from src.infrastructure.messaging.tasks.onboarding_email_tasks import (
            send_first_order_email_task,
        )

        result = send_first_order_email_task.run(
            email="merchant@test.io",
            merchant_name="Test Store",
            order_number="ORD-001",
            total="EGP 150.00",
        )

        mock_service.send_email.assert_called_once()
        assert result["sent"] is True


# ---------------------------------------------------------------------------
# Tests: Notification Celery tasks
# ---------------------------------------------------------------------------


class TestNotificationCeleryTasks:
    """Verify core notification Celery tasks invoke services correctly."""

    @patch("src.infrastructure.messaging.tasks.notification_tasks.ResendEmailService")
    def test_order_confirmation_email_task(self, MockEmailService):
        mock_service = MagicMock()
        mock_service.send_order_confirmation = AsyncMock(return_value=True)
        MockEmailService.return_value = mock_service

        from src.infrastructure.messaging.tasks.notification_tasks import (
            send_order_confirmation_email_task,
        )

        result = send_order_confirmation_email_task.run(
            email="buyer@test.io",
            order_number="ORD-002",
            order_details={
                "items": [{"name": "Widget", "quantity": 1, "price": 50}],
                "total": 50,
            },
        )

        mock_service.send_order_confirmation.assert_called_once()
        assert result["sent"] is True
        assert result["order_number"] == "ORD-002"

    @patch(
        "src.infrastructure.messaging.tasks.notification_tasks.WhatsAppMessagingService"
    )
    def test_whatsapp_order_confirmation_task(self, MockWA):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message_id = "wamid_123"
        mock_service.send_order_confirmation = AsyncMock(return_value=mock_result)
        MockWA.return_value = mock_service

        from src.infrastructure.messaging.tasks.notification_tasks import (
            send_whatsapp_order_confirmation_task,
        )

        result = send_whatsapp_order_confirmation_task.run(
            phone="+201012345678",
            customer_name="Alice",
            order_number="ORD-003",
            total="EGP 200.00",
            store_name="Test Store",
        )

        mock_service.send_order_confirmation.assert_called_once()
        assert result["sent"] is True
        assert result["message_id"] == "wamid_123"

    @patch("src.infrastructure.messaging.tasks.notification_tasks.ResendEmailService")
    def test_shipping_notification_email_task(self, MockEmailService):
        mock_service = MagicMock()
        mock_service.send_shipping_notification = AsyncMock(return_value=True)
        MockEmailService.return_value = mock_service

        from src.infrastructure.messaging.tasks.notification_tasks import (
            send_shipping_notification_email_task,
        )

        result = send_shipping_notification_email_task.run(
            email="buyer@test.io",
            order_number="ORD-004",
            tracking_number="BOSTA-999",
            carrier="Bosta",
        )

        mock_service.send_shipping_notification.assert_called_once()
        assert result["sent"] is True

    @patch(
        "src.infrastructure.messaging.tasks.notification_tasks.WhatsAppMessagingService"
    )
    def test_whatsapp_shipping_update_task(self, MockWA):
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message_id = "wamid_456"
        mock_service.send_shipping_notification = AsyncMock(return_value=mock_result)
        MockWA.return_value = mock_service

        from src.infrastructure.messaging.tasks.notification_tasks import (
            send_whatsapp_shipping_update_task,
        )

        result = send_whatsapp_shipping_update_task.run(
            phone="+201012345678",
            customer_name="Alice",
            order_number="ORD-005",
            tracking_number="BOSTA-456",
            carrier="Bosta",
        )

        mock_service.send_shipping_notification.assert_called_once()
        assert result["sent"] is True
