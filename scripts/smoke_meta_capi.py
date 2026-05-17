"""One-shot script: configure Meta tracking on Smoke Store + fire a test event.

Uses asyncpg + raw SQL to avoid SQLAlchemy's full-model-graph
resolution burden. Encrypts the CAPI token via the real SecretsManager
(matches the merchant-hub PUT path byte-for-byte). Enqueues the Celery
task via send_task() — the worker (already running) picks it up.

Phase 10c-d-e of the meta-pixels&CAPI plan.
"""

import asyncio
import json
import sys
import time
from uuid import uuid4

import asyncpg

# Stand-alone imports: SecretsManager + celery_app pull their own deps,
# they do NOT trigger ORM mapper resolution.
from src.infrastructure.external_services.secrets.secrets_manager import (
    get_secrets_manager,
)
from src.infrastructure.messaging.celery_app import celery_app

# ── Inputs from the user (Phase 10b) ───────────────────────────────────
STORE_SUBDOMAIN = "smoke-store"
PIXEL_ID = "1552896226251388"
CAPI_ACCESS_TOKEN = (
    "EAAOLtA4NeCgBRfiA0fQuqaaZCA0ZAeGoD2qzPbzA9PZBLdW4ZBBUTpa2NjxPMZBN7"
    "LWhpFthWsKAeXKuyFvu4jnRm6A8kqvBphjEyWtcZAEcJUS8S1dUkUss60ZCoNxYSR"
    "CrPaQ65UVMjxBeaH958GRZCLRP1GJm3HX1l17rZAthvPQpi0ubw5f760eFzW3GEmQ"
    "namwZDZD"
)
TEST_EVENT_CODE = "TEST27073"

DSN = "postgres://postgres:postgres@localhost:5432/numu"


async def main() -> int:
    conn = await asyncpg.connect(DSN)
    try:
        # 1. Find Smoke Store + its tenant
        row = await conn.fetchrow(
            "SELECT id, tenant_id, settings FROM stores WHERE subdomain = $1",
            STORE_SUBDOMAIN,
        )
        if not row:
            print(f"[smoke] FAIL: no store with subdomain={STORE_SUBDOMAIN}")
            return 2
        store_id = row["id"]
        tenant_id = row["tenant_id"]
        # asyncpg returns JSONB as a JSON string by default — parse it.
        current_settings = row["settings"]
        if isinstance(current_settings, str):
            current_settings = json.loads(current_settings)
        current_settings = current_settings or {}
        print(f"[smoke] store_id={store_id} tenant_id={tenant_id}")

        # 2. Encrypt the CAPI token via the real SecretsManager
        sm = get_secrets_manager()
        key_id = await sm.get_current_key_id()
        encrypted = await sm.encrypt({"access_token": CAPI_ACCESS_TOKEN}, key_id)
        # encrypted is bytes — store as bytea/text depending on column type.
        # Inspect to be safe:
        print(
            f"[smoke] token encrypted via key_id={key_id} "
            f"(type={type(encrypted).__name__}, len={len(encrypted)})"
        )

        # 3. Insert or update the service credential. Tenant context unset
        # here — set app.rls_bypass to true so we can write across tenants.
        await conn.execute("SELECT set_config('app.rls_bypass', 'true', true)")

        existing = await conn.fetchrow(
            """
            SELECT id FROM service_credentials
            WHERE tenant_id = $1
              AND service_type = 'tracking'
              AND service_name = 'meta_capi'
            """,
            tenant_id,
        )
        if existing:
            await conn.execute(
                """
                UPDATE service_credentials
                SET credentials_encrypted = $1,
                    encryption_key_id = $2,
                    is_active = true,
                    is_validated = false,
                    metadata = $3::jsonb,
                    updated_at = now()
                WHERE id = $4
                """,
                encrypted,
                key_id,
                json.dumps({"pixel_id": PIXEL_ID}),
                existing["id"],
            )
            print(f"[smoke] updated existing credential id={existing['id']}")
        else:
            new_id = uuid4()
            await conn.execute(
                """
                INSERT INTO service_credentials (
                    id, tenant_id, service_type, service_name,
                    credentials_encrypted, encryption_key_id,
                    is_active, is_validated, metadata,
                    created_at, updated_at
                ) VALUES ($1, $2, 'tracking', 'meta_capi', $3, $4, true, false,
                          $5::jsonb, now(), now())
                """,
                new_id,
                tenant_id,
                encrypted,
                key_id,
                json.dumps({"pixel_id": PIXEL_ID}),
            )
            print(f"[smoke] inserted new credential id={new_id}")

        # 4. Update store.settings.tracking.meta
        tracking = current_settings.get("tracking") or {}
        meta_cfg = tracking.get("meta") or {}
        meta_cfg.update(
            pixel_id=PIXEL_ID,
            pixel_enabled=True,
            capi_enabled=True,
            test_event_code=TEST_EVENT_CODE,
            consent_required=False,
            domain_verification_token=meta_cfg.get("domain_verification_token")
            or "smoke-domain-token-9f4a8b3c2e1d",
            debug_mode_expires_at=None,
        )
        tracking["meta"] = meta_cfg
        current_settings["tracking"] = tracking
        await conn.execute(
            "UPDATE stores SET settings = $1::jsonb, updated_at = now() WHERE id = $2",
            json.dumps(current_settings),
            store_id,
        )
        print(f"[smoke] store settings updated — pixel_id={PIXEL_ID} mode=both")

    finally:
        await conn.close()

    # 5. Fire a synthetic Purchase via Celery send_task (avoids importing
    # the task module which imports models).
    event_id = f"smoke-{uuid4()}"
    async_result = celery_app.send_task(
        "tasks.meta_capi_send_event",
        kwargs={
            "store_id": str(store_id),
            "pixel_id": PIXEL_ID,
            "event_name": "Purchase",
            "event_id": event_id,
            "event_time": int(time.time()),
            "event_source_url": f"http://{STORE_SUBDOMAIN}.localhost:3000/order-confirmation",
            "user_data": {
                "email": "smoke-buyer@example.com",
                "phone": "01001234567",
                "first_name": "Smoke",
                "last_name": "Buyer",
                "city": "Cairo",
                "country_code": "EG",
                "zip": "11511",
                "customer_id": "smoke-customer-1",
                "ip": "197.45.123.45",
                "user_agent": "Mozilla/5.0 (Smoke Test)",
                "fbp": "fb.1.1700000000000.smoketest",
                "fbc": None,
            },
            "custom_data": {
                "currency": "EGP",
                "value": 249.50,
                "content_ids": ["smoke-product-1"],
                "content_type": "product",
                "num_items": 1,
            },
            "test_event_code": TEST_EVENT_CODE,
            "action_source": "website",
        },
        queue="default",
    )
    print(f"[smoke] meta_capi_send_event enqueued task_id={async_result.id}")
    print(f"[smoke] event_id={event_id}")
    print("[smoke] watch the Celery worker log for the result.")
    print(f"[smoke] verify in Meta Events Manager > Test Events > {TEST_EVENT_CODE}.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
