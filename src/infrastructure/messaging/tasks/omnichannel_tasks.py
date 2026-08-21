"""Omnichannel Celery tasks."""

from src.infrastructure.messaging.celery_app import celery_app


@celery_app.task(name="omnichannel.refresh_token")
def refresh_meta_token(connection_id: str) -> dict:
    """Refresh Meta access token before expiry."""
    import asyncio

    from src.api.dependencies.repositories import (
        get_channel_connection_repository,
    )
    from src.infrastructure.external_services.meta import MetaOAuthService

    async def _refresh():
        channel_repo = get_channel_connection_repository()
        oauth = MetaOAuthService()

        connection = await channel_repo.get_by_id(connection_id)
        if not connection:
            return {"error": "Connection not found"}

        token = oauth.refresh_token(connection.encrypted_credentials)
        connection.encrypted_credentials = token["access_token"]
        if token.get("expires_in"):
            from datetime import datetime, timedelta

            connection.token_expires_at = datetime.utcnow() + timedelta(
                seconds=token["expires_in"]
            )
        await channel_repo.update(connection)
        return {"status": "Token refreshed"}

    return asyncio.run(_refresh())


@celery_app.task(
    name="omnichannel.backfill_conversations",
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 3},
)
def backfill_conversations(
    connection_id: str,
    since_days: int = 90,
    max_conversations: int = 100,
) -> dict:
    """Seed the inbox with a connection's existing Meta conversations.

    Runs after a merchant connects a Page/IG account so the inbox isn't
    empty on day one. Idempotent — messages are keyed on their Meta id,
    so retries and concurrent webhooks can't duplicate anything.
    """
    import asyncio
    from uuid import UUID as _UUID

    from sqlalchemy import text

    from src.application.use_cases.omnichannel.backfill_conversations import (
        BackfillConversationsUseCase,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories import (
        ChannelConnectionRepositoryImpl,
        ChannelMessageRepositoryImpl,
        MessageThreadRepositoryImpl,
    )

    async def _run() -> dict:
        async with AsyncSessionLocal() as session:
            # No tenant context in a background job; the connection row
            # carries the tenant_id every write is scoped to.
            await session.execute(text("SET search_path TO public"))
            await session.execute(
                text("SELECT set_config('app.rls_bypass', 'true', true)")
            )
            result = await BackfillConversationsUseCase(
                channel_connection_repository=ChannelConnectionRepositoryImpl(session),
                message_thread_repository=MessageThreadRepositoryImpl(session),
                channel_message_repository=ChannelMessageRepositoryImpl(session),
            ).execute(
                connection_id=_UUID(connection_id),
                since_days=since_days,
                max_conversations=max_conversations,
            )
            await session.commit()
            return result

    return asyncio.run(_run())


@celery_app.task(name="omnichannel.process_inbound")
def process_inbound_message(message_id: str) -> dict:
    """Process incoming message - create thread, notifications."""
    import asyncio

    from src.api.dependencies.repositories import (
        get_channel_message_repository,
        get_message_thread_repository,
    )
    from src.infrastructure.external_services.notifications import NotificationService

    async def _process():
        message = await get_channel_message_repository().get_by_id(message_id)
        if not message:
            return {"error": "Message not found"}

        thread = await get_message_thread_repository().get_by_id(message.thread_id)
        if thread:
            from src.infrastructure.realtime.redis_pubsub import RealtimePublisher

            await RealtimePublisher.publish(
                channel=f"store:{thread.store_id}:inbox",
                event="new_message",
                data={"thread_id": str(thread.id), "message_id": str(message.id)},
            )

        notify = NotificationService()
        await notify.send(
            recipient_id=thread.store_id,
            title="New message",
            body="You have a new message",
            channel="websocket",
        )

        return {"status": "Processed"}

    return asyncio.run(_process())


@celery_app.task(name="omnichannel.sync_catalog")
def sync_catalog_task(connection_id: str, store_id: str) -> dict:
    """Background catalog sync task."""
    import asyncio

    from src.api.dependencies.repositories import (
        get_catalog_mapping_repository,
        get_channel_connection_repository,
        get_product_repository,
    )
    from src.application.use_cases.omnichannel import SyncCatalogUseCase

    async def _sync():
        return await SyncCatalogUseCase(
            channel_connection_repository=get_channel_connection_repository(),
            catalog_mapping_repository=get_catalog_mapping_repository(),
            product_repository=get_product_repository(),
        ).execute(connection_id=connection_id, store_id=store_id, full_sync=False)

    return asyncio.run(_sync())


@celery_app.task(name="omnichannel.send_capi")
def send_capi_event_task(
    store_id: str,
    event_name: str,
    event_id: str,
    event_time: int,
    user_data: dict,
    custom_data: dict | None,
    event_source_url: str | None,
) -> dict:
    """Background CAPI event send."""
    import asyncio
    from uuid import UUID

    from src.api.dependencies.repositories import get_store_repository
    from src.application.use_cases.omnichannel import SendCapiEventUseCase

    async def _send():
        return await SendCapiEventUseCase(
            store_repository=get_store_repository(),
        ).execute(
            store_id=UUID(store_id),
            event_name=event_name,
            event_id=UUID(event_id),
            event_time=event_time,
            user_data=user_data,
            custom_data=custom_data,
            event_source_url=event_source_url,
        )

    return asyncio.run(_send())
