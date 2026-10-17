from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.application.use_cases.omnichannel.ingest_message import (
    IngestInboundMessageUseCase,
)
from src.core.entities.channel_connection import ChannelType


async def test_inbound_message_is_pushed_where_the_hub_inbox_listens() -> None:
    store_id = uuid4()
    connection = SimpleNamespace(
        tenant_id=uuid4(), store_id=store_id, channel=ChannelType.WHATSAPP
    )
    messages = AsyncMock()
    messages.get_by_external_id.return_value = None
    threads = AsyncMock()
    threads.get_by_connection_and_participant.return_value = None
    publisher = AsyncMock()

    await IngestInboundMessageUseCase(
        channel_connection_repository=AsyncMock(
            get_by_id=AsyncMock(return_value=connection)
        ),
        message_thread_repository=threads,
        channel_message_repository=messages,
        realtime_publisher=publisher,
    ).execute(
        connection_id=uuid4(),
        external_message_id="wamid.1",
        sender_id="201000000000",
        sender_name="Customer",
        message_type="text",
        body="hello",
        attachment_url=None,
        external_timestamp=1_760_000_000,
    )

    kwargs = publisher.publish.await_args.kwargs
    # Must match /ws/inbox/{store_id} (src/api/v1/routes/ws/inbox.py) and the
    # hub Inbox's `message.created` listener.
    assert kwargs["channel"] == f"store:{store_id}:inbox"
    assert kwargs["event"]["type"] == "message.created"
    assert kwargs["event"]["message"]["body"] == "hello"
    assert kwargs["event"]["message"]["direction"] == "inbound"
