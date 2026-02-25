"""Unit tests for MessageLogRepository."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.entities.message_log import (
    MessageDirection,
    MessageLog,
    MessageStatus,
)
from src.infrastructure.repositories.message_log_repository import (
    MessageLogRepository,
)


class TestMessageLogRepository:
    """Tests for MessageLogRepository CRUD and custom methods."""

    def setup_method(self) -> None:
        """Set up test fixtures with a mocked AsyncSession."""
        self.mock_session = MagicMock()
        self.mock_session.execute = AsyncMock()
        self.mock_session.add = MagicMock()
        self.mock_session.flush = AsyncMock()
        self.mock_session.refresh = AsyncMock()
        self.mock_session.delete = AsyncMock()
        self.repository = MessageLogRepository(self.mock_session)

    def _create_mock_model(self, **overrides):
        """Create a mock MessageLogModel with sensible defaults."""
        model = MagicMock()
        model.id = overrides.get("id", uuid4())
        model.tenant_id = overrides.get("tenant_id", uuid4())
        model.store_id = overrides.get("store_id", uuid4())
        model.phone = overrides.get("phone", "201098433918")
        model.metadata_ = overrides.get("metadata_", None)
        model.message_id = overrides.get("message_id", f"wamid.{uuid4().hex[:12]}")
        model.direction = overrides.get("direction", MessageDirection.OUTBOUND)
        model.template_name = overrides.get("template_name", "order_confirmation_en")
        model.content = overrides.get("content", "Test content")
        model.status = overrides.get("status", MessageStatus.SENT)
        model.error_code = overrides.get("error_code", None)
        model.created_at = overrides.get("created_at", datetime.now(UTC))
        model.updated_at = overrides.get("updated_at", datetime.now(UTC))
        return model

    # ------------------------------------------------------------------ #
    # get_by_id
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_get_by_id_found(self):
        """Returns entity when model exists."""
        log_id = uuid4()
        mock_model = self._create_mock_model(id=log_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_id(log_id)

        assert result is not None
        assert result.id == log_id

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self):
        """Returns None when no matching model."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_id(uuid4())
        assert result is None

    # ------------------------------------------------------------------ #
    # get_by_message_id
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_get_by_message_id_found(self):
        """Finds a log entry by provider message ID."""
        wa_id = "wamid.abc123"
        mock_model = self._create_mock_model(message_id=wa_id)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_message_id(wa_id)

        assert result is not None
        assert result.message_id == wa_id

    @pytest.mark.asyncio
    async def test_get_by_message_id_not_found(self):
        """Returns None for unknown provider message ID."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_by_message_id("wamid.doesnotexist")
        assert result is None

    # ------------------------------------------------------------------ #
    # get_by_store
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_get_by_store_returns_list(self):
        """Returns multiple logs for a store."""
        store_id = uuid4()
        models = [self._create_mock_model(store_id=store_id) for _ in range(3)]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = models
        self.mock_session.execute.return_value = mock_result

        results = await self.repository.get_by_store(store_id)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_get_by_store_with_direction_filter(self):
        """Filters by direction when provided."""
        store_id = uuid4()
        inbound = self._create_mock_model(
            store_id=store_id, direction=MessageDirection.INBOUND
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [inbound]
        self.mock_session.execute.return_value = mock_result

        results = await self.repository.get_by_store(
            store_id, direction=MessageDirection.INBOUND
        )
        assert len(results) == 1
        assert results[0].direction == MessageDirection.INBOUND

    # ------------------------------------------------------------------ #
    # get_by_phone
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_get_by_phone_returns_list(self):
        """Returns conversation history for a phone number."""
        store_id = uuid4()
        phone = "201098433918"
        models = [
            self._create_mock_model(store_id=store_id, phone=phone) for _ in range(2)
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = models
        self.mock_session.execute.return_value = mock_result

        results = await self.repository.get_by_phone(store_id, phone)
        assert len(results) == 2
        assert all(r.phone == phone for r in results)

    @pytest.mark.asyncio
    async def test_get_by_phone_respects_pagination(self):
        """Accepts skip and limit parameters."""
        store_id = uuid4()
        phone = "201098433918"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        self.mock_session.execute.return_value = mock_result

        results = await self.repository.get_by_phone(store_id, phone, skip=10, limit=5)
        assert results == []
        # The important thing is that the method accepted the params

    # ------------------------------------------------------------------ #
    # update_status
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_update_status_delivered(self):
        """Updates status from SENT to DELIVERED."""
        wa_id = "wamid.delivery_test"
        mock_model = self._create_mock_model(
            message_id=wa_id, status=MessageStatus.SENT
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.update_status(
            message_id=wa_id,
            status=MessageStatus.DELIVERED,
        )

        assert result is not None
        assert mock_model.status == MessageStatus.DELIVERED
        assert mock_model.error_code is None
        self.mock_session.flush.assert_awaited()
        self.mock_session.refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_status_failed_with_error_code(self):
        """Updates status to FAILED with an error code."""
        wa_id = "wamid.fail_test"
        mock_model = self._create_mock_model(
            message_id=wa_id, status=MessageStatus.SENT
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.update_status(
            message_id=wa_id,
            status=MessageStatus.FAILED,
            error_code="131047",
        )

        assert result is not None
        assert mock_model.status == MessageStatus.FAILED
        assert mock_model.error_code == "131047"

    @pytest.mark.asyncio
    async def test_update_status_not_found(self):
        """Returns None when message_id not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.update_status(
            message_id="wamid.ghost",
            status=MessageStatus.READ,
        )
        assert result is None

    # ------------------------------------------------------------------ #
    # update_status — regression guard
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_update_status_blocks_regression_read_to_delivered(self):
        """READ → DELIVERED is a regression and must be blocked."""
        wa_id = "wamid.regress"
        mock_model = self._create_mock_model(
            message_id=wa_id, status=MessageStatus.READ
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.update_status(
            message_id=wa_id,
            status=MessageStatus.DELIVERED,
        )

        assert result is not None
        # Status should NOT have changed
        assert mock_model.status == MessageStatus.READ
        self.mock_session.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_status_blocks_regression_delivered_to_sent(self):
        """DELIVERED → SENT is a regression and must be blocked."""
        wa_id = "wamid.regress2"
        mock_model = self._create_mock_model(
            message_id=wa_id, status=MessageStatus.DELIVERED
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.update_status(
            message_id=wa_id,
            status=MessageStatus.SENT,
        )

        assert result is not None
        assert mock_model.status == MessageStatus.DELIVERED
        self.mock_session.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_status_same_status_is_noop(self):
        """Same status is a no-op (not forward progress)."""
        wa_id = "wamid.same"
        mock_model = self._create_mock_model(
            message_id=wa_id, status=MessageStatus.DELIVERED
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.update_status(
            message_id=wa_id,
            status=MessageStatus.DELIVERED,
        )

        assert result is not None
        self.mock_session.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_status_failed_always_accepted(self):
        """FAILED is always accepted regardless of current status."""
        wa_id = "wamid.fail_from_read"
        mock_model = self._create_mock_model(
            message_id=wa_id, status=MessageStatus.READ
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.update_status(
            message_id=wa_id,
            status=MessageStatus.FAILED,
            error_code="131047",
        )

        assert result is not None
        assert mock_model.status == MessageStatus.FAILED
        assert mock_model.error_code == "131047"
        self.mock_session.flush.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_status_forward_progress_allowed(self):
        """QUEUED → SENT → DELIVERED → READ are all forward progress."""
        for current, new in [
            (MessageStatus.QUEUED, MessageStatus.SENT),
            (MessageStatus.SENT, MessageStatus.DELIVERED),
            (MessageStatus.DELIVERED, MessageStatus.READ),
        ]:
            session = MagicMock()
            session.execute = AsyncMock()
            session.flush = AsyncMock()
            session.refresh = AsyncMock()
            repo = MessageLogRepository(session)

            mock_model = self._create_mock_model(status=current)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_model
            session.execute.return_value = mock_result

            await repo.update_status(message_id="wamid.fwd", status=new)
            assert mock_model.status == new
            session.flush.assert_awaited()

    # ------------------------------------------------------------------ #
    # get_latest_by_phone
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_get_latest_by_phone_found(self):
        """Returns the most recent message for a phone number."""
        phone = "201098433918"
        mock_model = self._create_mock_model(phone=phone)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_model
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_latest_by_phone(phone)

        assert result is not None
        assert result.phone == phone

    @pytest.mark.asyncio
    async def test_get_latest_by_phone_not_found(self):
        """Returns None when no messages exist for the phone number."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        self.mock_session.execute.return_value = mock_result

        result = await self.repository.get_latest_by_phone("201000000000")
        assert result is None

    # ------------------------------------------------------------------ #
    # create
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_create_outbound_message(self):
        """Creates an outbound log entry."""
        store_id = uuid4()
        tenant_id = uuid4()
        entity = MessageLog(
            tenant_id=tenant_id,
            store_id=store_id,
            phone="201098433918",
            message_id="wamid.new_outbound",
            direction=MessageDirection.OUTBOUND,
            template_name="order_confirmation_en",
            content="Test params",
            status=MessageStatus.SENT,
        )

        # Mock refresh to keep the same model
        _mock_model = self._create_mock_model(
            id=entity.id,
            message_id=entity.message_id,
        )
        self.mock_session.refresh.side_effect = None

        await self.repository.create(entity)
        self.mock_session.add.assert_called_once()
        self.mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_inbound_message(self):
        """Creates an inbound log entry."""
        entity = MessageLog(
            tenant_id=uuid4(),
            store_id=uuid4(),
            phone="201098433918",
            message_id="wamid.new_inbound",
            direction=MessageDirection.INBOUND,
            content="مرحبا",
            status=MessageStatus.DELIVERED,
            metadata={"type": "text"},
        )

        await self.repository.create(entity)
        self.mock_session.add.assert_called_once()

    # ------------------------------------------------------------------ #
    # count
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_count(self):
        """Returns the total count of message logs."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = 42
        self.mock_session.execute.return_value = mock_result

        count = await self.repository.count()
        assert count == 42
