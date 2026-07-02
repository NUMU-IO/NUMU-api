"""TikTok Shop channel Celery tasks — order ingestion.

``tiktok_shop_ingest_order`` is enqueued by the webhook receiver on an
ORDER_STATUS_CHANGE event. It resolves the NUMU store from the shop_id,
decrypts the channel access token, fetches the full order detail from TikTok
Shop, and creates a native NUMU order via ``TikTokShopOrderIngestor``.

Fail-open + retrying: order-fetch/network errors retry; a resolved-but-
duplicate order is a silent no-op (ingestor dedups on external_order_id).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import sentry_sdk

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro: Any) -> Any:
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.tiktok_shop_ingest_order",
    bind=True,
    max_retries=5,
    default_retry_delay=15,
    autoretry_for=(httpx.NetworkError, httpx.TimeoutException),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def tiktok_shop_ingest_order(
    self: Any, *, shop_id: str, order_id: str
) -> dict[str, Any]:
    """Ingest one TikTok Shop order into NUMU. Returns a small status dict."""
    sentry_sdk.set_tag("tiktok_shop.shop_id", shop_id)
    sentry_sdk.set_tag("tiktok_shop.order_id", order_id)
    try:
        return _run_async(_ingest(shop_id=shop_id, order_id=order_id))
    except (httpx.NetworkError, httpx.TimeoutException):
        raise  # Celery autoretry
    except Exception:  # noqa: BLE001
        logger.exception("tiktok_shop_ingest_unexpected_error")
        return {"status": "failed"}


async def _resolve_store_by_shop_id(session, shop_id: str):
    """Find the store whose settings.channels.tiktok_shop.shop_id == shop_id."""
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.store import StoreModel

    q = select(StoreModel).where(
        StoreModel.settings["channels"]["tiktok_shop"]["shop_id"].as_string() == shop_id
    )
    try:
        return (await session.execute(q)).scalar_one_or_none()
    except Exception:  # noqa: BLE001 — JSONB path operator dialect differences
        # Fallback: scan a bounded set and match in Python.
        rows = (await session.execute(select(StoreModel).limit(2000))).scalars().all()
        for s in rows:
            cfg = ((s.settings or {}).get("channels") or {}).get("tiktok_shop") or {}
            if str(cfg.get("shop_id")) == shop_id:
                return s
        return None


async def _ingest(*, shop_id: str, order_id: str) -> dict[str, Any]:
    from src.application.services.tiktok_shop_order_ingestor import (
        TikTokShopOrderIngestor,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.external_services.secrets import get_secrets_manager
    from src.infrastructure.external_services.tiktok.shop_client import (
        TikTokShopClient,
    )
    from src.infrastructure.repositories.customer_repository import CustomerRepository
    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.store_repository import StoreRepository
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        store_model = await _resolve_store_by_shop_id(session, shop_id)
        if store_model is None:
            logger.warning("tiktok_shop_ingest_store_not_found", shop_id=shop_id)
            return {"status": "skipped", "reason": "store_not_found"}

        tenant_id = store_model.tenant_id
        cfg = ((store_model.settings or {}).get("channels") or {}).get(
            "tiktok_shop"
        ) or {}
        shop_cipher = cfg.get("shop_cipher") or ""

        # Decrypt the channel token.
        from sqlalchemy import select

        cred = (
            await session.execute(
                select(ServiceCredential)
                .where(ServiceCredential.tenant_id == tenant_id)
                .where(ServiceCredential.service_type == ServiceType.SALES_CHANNEL)
                .where(ServiceCredential.service_name == ServiceName.TIKTOK_SHOP)
                .where(ServiceCredential.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if cred is None:
            return {"status": "skipped", "reason": "credential_missing"}

        secrets = get_secrets_manager()
        try:
            decrypted = await secrets.decrypt(
                cred.credentials_encrypted, cred.encryption_key_id
            )
            access_token = decrypted["access_token"]
        except Exception:  # noqa: BLE001
            logger.exception("tiktok_shop_token_decrypt_failed", shop_id=shop_id)
            return {"status": "failed", "reason": "decrypt_error"}

    # Fetch order detail outside the DB session (network round trip).
    client = TikTokShopClient()
    orders = await client.get_order_detail(
        access_token=access_token,
        shop_cipher=shop_cipher,
        order_ids=[order_id],
    )
    if not orders:
        return {"status": "skipped", "reason": "order_not_found"}

    # Ingest under the tenant context + narrowed repos.
    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        await narrow_to_tenant(session, tenant_id)
        store = await StoreRepository(session).get_by_id(store_model.id)
        if store is None:
            return {"status": "skipped", "reason": "store_not_found"}
        ingestor = TikTokShopOrderIngestor(
            order_repo=OrderRepository(session),
            customer_repo=CustomerRepository(session),
            event_bus=None,  # channel notifications are a follow-up
        )
        numu_order_id = await ingestor.ingest(store=store, tiktok_order=orders[0])
        await session.commit()

    if numu_order_id is None:
        return {"status": "duplicate_or_unmapped"}
    return {"status": "ingested", "order_id": numu_order_id}
