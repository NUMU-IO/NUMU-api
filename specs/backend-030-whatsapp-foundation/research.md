# Phase 0 — Research

**Feature**: WhatsApp Integration Phase 1 — Backend Foundation
**Date**: 2026-05-24

The five spec-level `[NEEDS CLARIFICATION]` candidates were resolved interactively via `/speckit-clarify` and are recorded in `spec.md > ## Clarifications > Session 2026-05-24`. This document covers operational research items uncovered during planning.

---

## R1. Meta `message_template_status_update` webhook field

**Decision**: Subscribe NUMU's platform Meta app to the `message_template_status_update` webhook field for each WABA the platform manages. Add a payload handler keyed off the `entity` (`waba_id`) → store mapping. For **BYO** stores, the merchant's own Meta app handles its own webhooks — NUMU cannot subscribe; we rely solely on the polling sync action (FR-028) for BYO templates.

**Rationale**: Meta delivers template status changes (`APPROVED`, `REJECTED`, `FLAGGED`, `PAUSED`, `DISABLED`) on the configured app's webhook URL. The subscription is done via the Graph API endpoint `POST /{waba_id}/subscribed_apps` with the app's access token, and the webhook field `message_template_status_update` is enabled on the app's webhook config (one-time per app). For platform-managed stores this is fully under NUMU's control. For BYO stores, the merchant owns their app and webhook — NUMU intentionally does NOT request them to set up a webhook to NUMU; that would require Meta App Review and is out of scope. Polling sync is the BYO path.

**Webhook payload (excerpt)**:
```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "<waba_id>",
    "changes": [{
      "field": "message_template_status_update",
      "value": {
        "event": "APPROVED|REJECTED|FLAGGED|PAUSED|DISABLED",
        "message_template_id": "<meta_template_id>",
        "message_template_name": "...",
        "message_template_language": "en|ar|...",
        "reason": "ABUSIVE_CONTENT|INVALID_FORMAT|null",
        "disable_info": { "disable_date": "..." }
      }
    }]
  }]
}
```

**Alternatives considered**:
- Polling-only (rejected — UX inferior; clarification Q3 chose webhook + polling fallback)
- Webhook-only (rejected — fragile for missed deliveries)

**Open implementation detail**: the existing `/webhooks/whatsapp/callback` route handles message events; template-status events must be **routed** by the top-level `field` value into the new `whatsapp_template_status_webhook` handler. Same route, different field router.

---

## R2. Meta BYO credential validation — exact calls

**Decision**: Three sequential GET calls against the merchant's submitted access token. All three must return 200 with expected fields. Failure of any short-circuits and returns a structured `BYOValidationFailure` to the caller.

| Step | Endpoint | Expected | Failure → user-facing error |
|------|----------|----------|------------------------------|
| 1 | `GET /{phone_number_id}?fields=verified_name,display_phone_number,quality_rating,code_verification_status` | 200 + `verified_name` non-null | `phone_number_unreachable` — token does not resolve phone_number_id |
| 2 | `GET /{waba_id}?fields=id,name,owner_business_info,timezone_id` | 200 + `id == waba_id` | `waba_mismatch` — token is for a different WABA |
| 3 | `GET /{waba_id}/message_templates?limit=1` | 200 + `data` array (may be empty) | `insufficient_scope` — token lacks `whatsapp_business_management` |

**Rationale**: These three reads exercise both required scopes (`whatsapp_business_messaging` via phone read; `whatsapp_business_management` via WABA + template-list read) and verify the cross-ID consistency that catches the most common BYO failure mode (a merchant copies token from one app + phone_number_id from another). No actual message is sent.

**Scope strings** (referenced in FR-021): `whatsapp_business_management`, `whatsapp_business_messaging`. Meta does not expose a token-introspection endpoint that lists scopes directly; the three reads function as scope tests by exercising one operation per required scope.

**Alternatives considered**:
- Two-step (phone + WABA, skip template list) — misses tokens with management-revoked scope.
- Four-step (add `POST /{phone_number_id}/register` test) — has side effects; rejected.

---

## R3. Celery exponential backoff pattern in this codebase

**Decision**: Use Celery's built-in `autoretry_for` + `retry_backoff` + `retry_backoff_max` + `retry_jitter` parameters. Reference config:

```python
@celery_app.task(
    name="numu_api.whatsapp.dispatch_scheduled_send",
    bind=True,
    autoretry_for=(httpx.HTTPStatusError, httpx.NetworkError),
    retry_backoff=True,           # exponential: 1, 2, 4, 8, 16... seconds
    retry_backoff_max=600,        # cap each retry delay at 10 min
    retry_jitter=True,            # ±50% jitter to avoid thundering herd
    max_retries=5,                # 5 retries spanning ~25 minutes worst case
)
async def dispatch_scheduled_send(self, scheduled_send_id: str) -> None: ...
```

5 retries × backoff cap = covers FR-031's "at least 3 attempts over at least 5 minutes" with margin. Non-retriable errors (HTTP 400 with Meta's `error.code in {131000..131999}` for "user opted out", "invalid template", etc.) raise a custom `NonRetriableWhatsAppError` that Celery does NOT autoretry — caught by the task body and routed straight to dead-letter.

**Rationale**: Built-in Celery autoretry avoids hand-rolled retry loops and integrates with the existing Flower observability. The existing `whatsapp_campaign_tasks.py` and `abandoned_cart_tasks.py` use `max_retries=1` — both will be migrated to this pattern in the same PR.

**Alternatives considered**:
- Hand-rolled `try/except` + `task.retry()` — rejected as more error-prone than declarative `autoretry_for`.
- Hand-rolled backoff with `time.sleep` — never; would block the worker.

---

## R4. PostgreSQL RLS policy template

**Decision**: Mirror the existing RLS pattern used by `whatsapp_conversations` and `whatsapp_campaigns` (which already enable RLS by `store_id`). The shared session-variable convention in this repo is `app.current_store_id`, set per-request via the existing middleware in `src/api/middleware/tenant_context.py`.

**Migration template** (applied to each of the three new tables):

```python
# in alembic/versions/20260524_add_whatsapp_optin_scheduled_dl.py
op.execute("ALTER TABLE whatsapp_opt_ins ENABLE ROW LEVEL SECURITY")
op.execute("""
    CREATE POLICY whatsapp_opt_ins_tenant_isolation ON whatsapp_opt_ins
    USING (store_id = current_setting('app.current_store_id', true)::uuid)
""")
# down(): DROP POLICY then ALTER TABLE ... DISABLE ROW LEVEL SECURITY
```

Celery workers and the scheduled-send dispatcher MUST set `app.current_store_id` before each per-store DB operation (existing pattern in `whatsapp_campaign_tasks.py` is followed).

**Rationale**: Consistency with existing tenant tables; no special-case for these three. Tests in `tests/security/test_rls_whatsapp.py` verify cross-store reads return empty.

---

## R5. Idempotency for OrderCreatedEvent / OrderPaidEvent → WhatsApp dedup

**Decision**: Use the existing `message_log` table as the dedup source. Before invoking the send, the handler queries `message_log` for any row matching `(store_id, customer_phone, template_name='order_confirmation', metadata->>'order_id' = <order_id>, status IN ('sent', 'delivered', 'read'))`. If a row exists, skip with reason `already_sent`. No new dedup table.

**Rationale**: `message_log` is already the audit-of-record for every WhatsApp send. A duplicate event replay must not produce a duplicate user-visible message; querying `message_log` for the same (order_id, event-type) tuple is correct and cheap (indexed by `store_id + customer_phone + created_at`; the metadata JSONB lookup is already used elsewhere). Adding a separate dedup table would duplicate state.

**Indexing**: Add a GIN index on `message_log.metadata` if not present (`CREATE INDEX IF NOT EXISTS ix_message_log_metadata_gin ON message_log USING gin (metadata)`). Check existence first — if it does, skip the migration step.

**Alternatives considered**:
- New `whatsapp_send_idempotency_keys` table keyed on `(store_id, idempotency_key=order_id:event_type)` — rejected; redundant with `message_log`.
- In-memory dedup (Redis SETNX) — rejected; loses dedup across worker restarts.

---

## R6. Storefront opt-in capture path (backend slice of Phase 2 UI work)

**Decision**: Add `POST /api/v1/storefront/{store_slug}/whatsapp/opt-in` (storefront-facing, anonymous body). Callable from the storefront checkout step when the consent checkbox is ticked. Body: `{ phone, customer_id_hint?, locale }`. The handler canonicalizes phone via `whatsapp_phone_formatter` and writes a `whatsapp_opt_ins` row with `source='checkout'`.

**Rationale**: Phase 2 will add the checkbox UI, but the backend endpoint can ship in Phase 1 so the storefront teams can wire it up in parallel. This is a guest-facing storefront endpoint (no merchant-hub auth required); authenticated by a public store slug and rate-limited via the existing `rate_limit_storefront` middleware.

---

## R7. STOP keyword detection — Arabic normalization

**Decision**: Normalize inbound message text via Unicode NFKC + strip diacritics (tashkeel) + strip leading/trailing whitespace + lowercase (for Latin) before the first-word check. The canonical opt-out set is `{"stop", "unsubscribe", "إلغاء", "الغاء"}`. The detector returns true iff `first_word ∈ canonical_set` (case-insensitive on Latin; Arabic tashkeel-stripped).

**Rationale**: WhatsApp customers will type `إلغاء` (with hamza) or `الغاء` (without) interchangeably. Common dialectal variants like `بطل` and `وقف` were considered but excluded from v1 — too many false positives in conversational Arabic. They can be added behind a per-store config in a later phase if real customer messages warrant it.

**Alternatives considered**:
- Regex-based fuzzy match — rejected; high false-positive risk.
- ML-based intent detection — rejected; gross over-engineering for "first word is one of four strings."

---

## R8. Dead-letter purge schedule

**Decision**: Celery Beat task `numu_api.whatsapp.purge_dead_letters` running daily at 03:00 UTC. Selects `WHERE created_at < NOW() - INTERVAL '90 days'`. Batches of 1000 with `LIMIT` + `OFFSET 0` (refresh between batches) to avoid long locks. Counts purged per day are logged.

**Rationale**: 03:00 UTC = 05:00 Cairo time — off-peak for Egyptian commerce. Daily run keeps purge work small (typical day adds <100 rows).

---

## Open Implementation Notes (not blocking plan)

- **Phone canonicalization edge case**: `whatsapp_phone_formatter` returns `None` for unparsable input. Opt-in write must reject (HTTP 422) rather than store a NULL phone — this is enforced in the schema (FR-006 + Pydantic v2 validator).
- **Customer.metadata `whatsapp_prefs` shape**: the existing `OrderStatusChangedEvent.whatsapp_prefs` dict carries `shipping_update` / `delivery_confirmation` flags. The new `order_created` and `order_paid` handlers will read `order_confirmation` and `payment_received` flags from the same dict, defaulting to `True` (utility default-on per FR-019a for platform-managed stores).
- **Per-store BYO mode determination**: `mode = "byo" if active service_credentials row with service_name=WHATSAPP_BUSINESS else "platform_managed"`. The status endpoint exposes this; the resolver already implements it.

---

All Phase 0 research items resolved. Proceeding to Phase 1.
