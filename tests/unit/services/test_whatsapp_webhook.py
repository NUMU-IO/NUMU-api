"""Unit tests for WhatsApp webhook handler and message logging.

Covers:
- Status update callbacks (sent, delivered, read, failed)
- Inbound customer message logging
- Outbound send logging via _log_outbound
- Error resilience (logging failures don't break the flow)
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.entities.message_log import (
    MessageDirection,
    MessageLog,
    MessageStatus as LogStatus,
)
from src.infrastructure.external_services.whatsapp.messaging_service import (
    WhatsAppMessagingService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PHONE = "201098433918"
STORE_ID = uuid4()
TENANT_ID = uuid4()


def _make_service() -> WhatsAppMessagingService:
    """Build a WhatsAppMessagingService with test credentials."""
    svc = WhatsAppMessagingService(
        access_token="test_token",
        phone_number_id="123456789",
        business_account_id="987654321",
        app_secret="test_secret",
    )
    svc.enabled = True
    return svc


def _make_repo(prior_message: MessageLog | None = None) -> MagicMock:
    """Build a mock MessageLogRepository with async helpers."""
    repo = MagicMock()
    repo.create = AsyncMock()
    repo.update_status = AsyncMock()
    repo.get_latest_by_phone = AsyncMock(return_value=prior_message)
    return repo


def _status_webhook(message_id: str, status: str, errors: list | None = None) -> dict:
    """Build a minimal WhatsApp status-update webhook payload."""
    status_obj: dict = {
        "id": message_id,
        "status": status,
        "recipient_id": PHONE,
    }
    if errors:
        status_obj["errors"] = errors
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [status_obj],
                        },
                    }
                ],
            }
        ],
    }


def _inbound_webhook(
    msg_id: str,
    from_number: str,
    msg_type: str = "text",
    body: str = "مرحبا",
) -> dict:
    """Build a minimal WhatsApp inbound message webhook payload."""
    message: dict = {
        "from": from_number,
        "id": msg_id,
        "type": msg_type,
    }
    if msg_type == "text":
        message["text"] = {"body": body}
    elif msg_type == "button":
        message["button"] = {"text": body}
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


# ===========================================================================
# Status update callbacks
# ===========================================================================


class TestStatusUpdateCallbacks:
    """handle_webhook_event → status updates persist via repo.update_status."""

    @pytest.mark.asyncio
    async def test_delivered_status_updates_log(self):
        """DELIVERED callback calls update_status with DELIVERED."""
        service = _make_service()
        repo = _make_repo()
        msg_id = "wamid.delivered_001"

        await service.handle_webhook_event(
            data=_status_webhook(msg_id, "delivered"),
            message_log_repo=repo,
        )

        repo.update_status.assert_awaited_once_with(
            message_id=msg_id,
            status=LogStatus.DELIVERED,
            error_code=None,
        )

    @pytest.mark.asyncio
    async def test_read_status_updates_log(self):
        """READ callback calls update_status with READ."""
        service = _make_service()
        repo = _make_repo()
        msg_id = "wamid.read_001"

        await service.handle_webhook_event(
            data=_status_webhook(msg_id, "read"),
            message_log_repo=repo,
        )

        repo.update_status.assert_awaited_once_with(
            message_id=msg_id,
            status=LogStatus.READ,
            error_code=None,
        )

    @pytest.mark.asyncio
    async def test_sent_status_updates_log(self):
        """SENT callback calls update_status with SENT."""
        service = _make_service()
        repo = _make_repo()
        msg_id = "wamid.sent_001"

        await service.handle_webhook_event(
            data=_status_webhook(msg_id, "sent"),
            message_log_repo=repo,
        )

        repo.update_status.assert_awaited_once_with(
            message_id=msg_id,
            status=LogStatus.SENT,
            error_code=None,
        )

    @pytest.mark.asyncio
    async def test_failed_status_updates_log_with_error_code(self):
        """FAILED callback extracts error code from errors array."""
        service = _make_service()
        repo = _make_repo()
        msg_id = "wamid.fail_001"

        await service.handle_webhook_event(
            data=_status_webhook(
                msg_id,
                "failed",
                errors=[{"code": 131047, "title": "Re-engagement message"}],
            ),
            message_log_repo=repo,
        )

        repo.update_status.assert_awaited_once_with(
            message_id=msg_id,
            status=LogStatus.FAILED,
            error_code="131047",
        )

    @pytest.mark.asyncio
    async def test_failed_status_no_errors_array(self):
        """FAILED callback without errors array sends error_code=None."""
        service = _make_service()
        repo = _make_repo()
        msg_id = "wamid.fail_no_err"

        await service.handle_webhook_event(
            data=_status_webhook(msg_id, "failed", errors=[]),
            message_log_repo=repo,
        )

        repo.update_status.assert_awaited_once_with(
            message_id=msg_id,
            status=LogStatus.FAILED,
            error_code=None,
        )

    @pytest.mark.asyncio
    async def test_unknown_status_ignored(self):
        """Unknown status value does not call update_status."""
        service = _make_service()
        repo = _make_repo()

        await service.handle_webhook_event(
            data=_status_webhook("wamid.unknown", "pending"),
            message_log_repo=repo,
        )

        repo.update_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status_update_without_repo_is_noop(self):
        """When no repo is provided, status updates are only logged."""
        service = _make_service()

        # Should not raise
        await service.handle_webhook_event(
            data=_status_webhook("wamid.no_repo", "delivered"),
            message_log_repo=None,
        )

    @pytest.mark.asyncio
    async def test_status_update_repo_error_is_swallowed(self):
        """If update_status raises, the webhook still processes."""
        service = _make_service()
        repo = _make_repo()
        repo.update_status.side_effect = RuntimeError("DB down")

        # Should not raise
        await service.handle_webhook_event(
            data=_status_webhook("wamid.db_err", "delivered"),
            message_log_repo=repo,
        )


# ===========================================================================
# Inbound customer messages
# ===========================================================================


class TestInboundMessageLogging:
    """handle_webhook_event → inbound messages persist via repo.create."""

    @pytest.mark.asyncio
    async def test_inbound_text_message_logged(self):
        """Text message from customer creates INBOUND log entry."""
        service = _make_service()
        repo = _make_repo()
        msg_id = "wamid.inbound_text_001"

        await service.handle_webhook_event(
            data=_inbound_webhook(msg_id, PHONE, "text", "كم سعر المنتج؟"),
            message_log_repo=repo,
            store_id=STORE_ID,
            tenant_id=TENANT_ID,
        )

        repo.create.assert_awaited_once()
        created_entity: MessageLog = repo.create.call_args[0][0]
        assert created_entity.direction == MessageDirection.INBOUND
        assert created_entity.phone == PHONE
        assert created_entity.message_id == msg_id
        assert created_entity.status == LogStatus.DELIVERED
        assert created_entity.content == "كم سعر المنتج؟"
        assert created_entity.store_id == STORE_ID
        assert created_entity.tenant_id == TENANT_ID
        assert created_entity.metadata == {"type": "text"}

    @pytest.mark.asyncio
    async def test_inbound_button_message_logged(self):
        """Button reply from customer creates INBOUND log entry."""
        service = _make_service()
        repo = _make_repo()

        await service.handle_webhook_event(
            data=_inbound_webhook("wamid.btn_001", PHONE, "button", "نعم"),
            message_log_repo=repo,
            store_id=STORE_ID,
            tenant_id=TENANT_ID,
        )

        repo.create.assert_awaited_once()
        entity: MessageLog = repo.create.call_args[0][0]
        assert entity.content == "نعم"
        assert entity.metadata == {"type": "button"}

    @pytest.mark.asyncio
    async def test_inbound_without_store_id_resolves_from_prior(self):
        """When store_id is not provided, it is resolved from prior messages."""
        service = _make_service()
        prior = MessageLog(
            tenant_id=TENANT_ID,
            store_id=STORE_ID,
            phone=PHONE,
            message_id="wamid.prior_outbound",
            direction=MessageDirection.OUTBOUND,
            status=LogStatus.SENT,
        )
        repo = _make_repo(prior_message=prior)

        await service.handle_webhook_event(
            data=_inbound_webhook("wamid.resolved", PHONE),
            message_log_repo=repo,
            store_id=None,
            tenant_id=None,
        )

        repo.get_latest_by_phone.assert_awaited_once_with(PHONE)
        repo.create.assert_awaited_once()
        entity: MessageLog = repo.create.call_args[0][0]
        assert entity.store_id == STORE_ID
        assert entity.tenant_id == TENANT_ID

    @pytest.mark.asyncio
    async def test_inbound_without_store_id_no_prior_skips(self):
        """When store_id is None and no prior messages exist, logging is skipped."""
        service = _make_service()
        repo = _make_repo(prior_message=None)

        await service.handle_webhook_event(
            data=_inbound_webhook("wamid.no_prior", PHONE),
            message_log_repo=repo,
            store_id=None,
            tenant_id=None,
        )

        repo.get_latest_by_phone.assert_awaited_once_with(PHONE)
        repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inbound_repo_error_is_swallowed(self):
        """If repo.create raises on inbound, webhook still succeeds."""
        service = _make_service()
        repo = _make_repo()
        repo.create.side_effect = RuntimeError("DB constraint violation")

        # Should not raise
        await service.handle_webhook_event(
            data=_inbound_webhook("wamid.err", PHONE),
            message_log_repo=repo,
            store_id=STORE_ID,
            tenant_id=TENANT_ID,
        )


# ===========================================================================
# Outbound logging via _log_outbound
# ===========================================================================


class TestOutboundLogging:
    """_log_outbound creates an OUTBOUND MessageLog entry."""

    @pytest.mark.asyncio
    async def test_log_outbound_creates_entity(self):
        """Successful send creates an outbound log entry."""
        service = _make_service()
        repo = _make_repo()

        await service._log_outbound(
            repo,
            store_id=STORE_ID,
            tenant_id=TENANT_ID,
            phone=PHONE,
            message_id="wamid.outbound_001",
            template_name="order_confirmation_en",
            content="{'order_number': 'ORD-123'}",
        )

        repo.create.assert_awaited_once()
        entity: MessageLog = repo.create.call_args[0][0]
        assert entity.direction == MessageDirection.OUTBOUND
        assert entity.status == LogStatus.SENT
        assert entity.phone == PHONE
        assert entity.message_id == "wamid.outbound_001"
        assert entity.template_name == "order_confirmation_en"
        assert entity.store_id == STORE_ID
        assert entity.tenant_id == TENANT_ID

    @pytest.mark.asyncio
    async def test_log_outbound_error_is_swallowed(self):
        """If repo.create raises, _log_outbound does not propagate."""
        service = _make_service()
        repo = _make_repo()
        repo.create.side_effect = RuntimeError("DB error")

        # Should not raise
        await service._log_outbound(
            repo,
            store_id=STORE_ID,
            tenant_id=TENANT_ID,
            phone=PHONE,
            message_id="wamid.err_outbound",
            template_name="shipping_en",
            content="test",
        )


# ===========================================================================
# send_and_log integration
# ===========================================================================


class TestSendAndLog:
    """send_and_log() wraps send_message + outbound logging."""

    @pytest.mark.asyncio
    async def test_send_and_log_creates_outbound_on_success(self):
        """After a 200 response, outbound log is created via send_and_log."""
        service = _make_service()
        repo = _make_repo()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.send_log_001"}],
            }
            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            from src.core.interfaces.services.messaging_service import (
                MessageContent,
                MessageRecipient,
                MessageType,
            )

            content = MessageContent(
                type=MessageType.ORDER_CONFIRMATION,
                recipient=MessageRecipient(
                    phone="01098433918", name="Customer", language="en"
                ),
                template_params={
                    "customer_name": "Customer",
                    "order_number": "ORD-001",
                    "total": "250 EGP",
                    "store_name": "Test Store",
                },
            )

            result = await service.send_and_log(
                content,
                repo=repo,
                store_id=STORE_ID,
                tenant_id=TENANT_ID,
            )

            assert result.success is True
            repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_and_log_skips_logging_on_failure(self):
        """When send fails, no outbound log is created."""
        service = _make_service()
        repo = _make_repo()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.json.return_value = {
                "error": {"message": "Bad request", "code": 100},
            }
            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            from src.core.interfaces.services.messaging_service import (
                MessageContent,
                MessageRecipient,
                MessageType,
            )

            content = MessageContent(
                type=MessageType.ORDER_CONFIRMATION,
                recipient=MessageRecipient(
                    phone="01098433918", name="Customer", language="en"
                ),
                template_params={
                    "customer_name": "Customer",
                    "order_number": "ORD-002",
                    "total": "300 EGP",
                    "store_name": "Test Store",
                },
            )

            result = await service.send_and_log(
                content,
                repo=repo,
                store_id=STORE_ID,
                tenant_id=TENANT_ID,
            )

            assert result.success is False
            repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_message_does_not_log(self):
        """Plain send_message (interface-conformant) never logs."""
        service = _make_service()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "messages": [{"id": "wamid.no_log_001"}],
            }
            mock_client = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            from src.core.interfaces.services.messaging_service import (
                MessageContent,
                MessageRecipient,
                MessageType,
            )

            content = MessageContent(
                type=MessageType.ORDER_CONFIRMATION,
                recipient=MessageRecipient(
                    phone="01098433918", name="Customer", language="en"
                ),
                template_params={
                    "customer_name": "Customer",
                    "order_number": "ORD-003",
                    "total": "300 EGP",
                    "store_name": "Test Store",
                },
            )

            result = await service.send_message(content)
            assert result.success is True


# ===========================================================================
# Mixed payloads (statuses + messages in one webhook)
# ===========================================================================


class TestMixedWebhookPayload:
    """Webhook containing both status updates and inbound messages."""

    @pytest.mark.asyncio
    async def test_mixed_payload_processes_both(self):
        """Status updates AND inbound messages are processed."""
        service = _make_service()
        repo = _make_repo()

        data = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "statuses": [
                                    {
                                        "id": "wamid.status_in_mix",
                                        "status": "read",
                                        "recipient_id": PHONE,
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": PHONE,
                                        "id": "wamid.msg_in_mix",
                                        "type": "text",
                                        "text": {"body": "Thanks!"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        await service.handle_webhook_event(
            data=data,
            message_log_repo=repo,
            store_id=STORE_ID,
            tenant_id=TENANT_ID,
        )

        # Status update
        repo.update_status.assert_awaited_once_with(
            message_id="wamid.status_in_mix",
            status=LogStatus.READ,
            error_code=None,
        )
        # Inbound message
        repo.create.assert_awaited_once()
        entity: MessageLog = repo.create.call_args[0][0]
        assert entity.direction == MessageDirection.INBOUND
        assert entity.message_id == "wamid.msg_in_mix"
