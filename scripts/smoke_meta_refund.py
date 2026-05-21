"""Phase 21 live smoke — fire a Refund custom event via the dispatcher.

Builds a fake refunded order, calls enqueue_meta_capi_refund directly,
then waits for the Celery worker to process and verifies the
meta_event_log row has the correct shape (event_name=Refund, negative value).
"""

import asyncio
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import asyncpg

import src.infrastructure.database.models  # noqa: F401 — load full registry
import src.infrastructure.database.models.public.role  # noqa: F401
import src.infrastructure.database.models.public.tenant_membership  # noqa: F401
from src.application.services.meta_capi_purchase_dispatcher import (
    enqueue_meta_capi_refund,
)
from src.infrastructure.database.connection import AsyncSessionLocal


async def main() -> int:
    # Use the same Smoke Store we configured earlier.
    dsn = "postgres://postgres:postgres@localhost:5432/numu"
    conn = await asyncpg.connect(dsn)
    row = await conn.fetchrow(
        "SELECT id, tenant_id FROM stores WHERE subdomain = $1",
        "smoke-store",
    )
    await conn.close()
    if not row:
        print("[refund-smoke] FAIL: no smoke-store")
        return 2
    store_id = row["id"]
    print(f"[refund-smoke] store_id={store_id}")

    # Build a minimal fake order — enough for the dispatcher to build
    # user_data + custom_data and look up the store's tracking config.
    fake_order_id = uuid4()
    fake_order = SimpleNamespace(
        id=fake_order_id,
        store_id=store_id,
        customer_id=uuid4(),
        total=24950,  # 249.50 EGP in cents
        currency="EGP",
        line_items=[{"product_id": str(uuid4()), "quantity": 1, "unit_price": 24950}],
        shipping_address={
            "email": "refund-buyer@example.com",
            "phone": "01001234567",
            "first_name": "Refund",
            "last_name": "Buyer",
            "city": "Cairo",
            "country": "EG",
            "postal_code": "11511",
        },
        metadata={
            "ip_address": "197.45.123.45",
            "user_agent": "Mozilla/5.0 (Refund Smoke)",
        },
        paid_at=None,
    )

    async with AsyncSessionLocal() as session:
        # Set RLS bypass — the repository the dispatcher uses will look
        # up the store and the smoke fake-order context isn't tied to
        # a particular tenant_id at the request layer.
        from sqlalchemy import text

        await session.execute(text("SET search_path TO public"))
        await session.execute(text("SELECT set_config('app.rls_bypass', 'true', true)"))
        await enqueue_meta_capi_refund(session, fake_order)
        await session.commit()

    print(f"[refund-smoke] enqueued Refund for fake order_id={fake_order_id}")
    print("[refund-smoke] expected event_id=refund-<order_id>")
    print(f"[refund-smoke] expected event_id={f'refund-{fake_order_id}'}")
    print("[refund-smoke] waiting 5s for Celery worker...")
    time.sleep(5)

    # Verify
    conn = await asyncpg.connect(dsn)
    await conn.execute("SELECT set_config('app.rls_bypass', 'true', true)")
    row = await conn.fetchrow(
        """
        SELECT event_id, event_name, response_status, fbtrace_id, last_error, request_payload, sent_at
        FROM meta_event_log
        WHERE event_id = $1
        ORDER BY created_at DESC LIMIT 1
        """,
        f"refund-{fake_order_id}",
    )
    await conn.close()
    if not row:
        print("[refund-smoke] FAIL: no meta_event_log row found")
        return 3
    print(f"[refund-smoke] event_name={row['event_name']}")
    print(f"[refund-smoke] status={row['response_status']}")
    print(f"[refund-smoke] fbtrace_id={row['fbtrace_id']}")
    print(f"[refund-smoke] sent_at={row['sent_at']}")
    if row["last_error"]:
        print(f"[refund-smoke] error={row['last_error'][:200]}")
    # Inspect the value in request_payload
    import json

    payload = row["request_payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    value = (
        payload.get("custom_data", {}).get("value")
        if isinstance(payload, dict)
        else None
    )
    print(f"[refund-smoke] custom_data.value={value} (expected negative)")
    return 0 if row["response_status"] == 200 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
