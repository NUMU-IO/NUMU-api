# API Contracts: COD Autopilot (004-cod-autopilot)

All endpoints follow existing conventions: store-scoped router, `get_current_store` auth dependency, responses wrapped per platform standard, Pydantic v2 validation at the boundary. Additive only — no existing endpoint changes shape (contract-versioning rule respected).

## 1. Settings

### `GET /api/v1/stores/{store_id}/settings/cod-autopilot`

Response `200`:
```json
{
  "enabled": false,
  "digest_hour": 18,
  "delivery_check_delay_days": 3,
  "delivery_check_retry_days": 2,
  "delivery_check_max_attempts": 3,
  "assumed_delivered_days": 10,
  "digest_deliverable": true,
  "auto_rto_days": 14
}
```
`digest_deliverable` and `auto_rto_days` are read-only (computed). Missing settings section → defaults returned.

### `PATCH /api/v1/stores/{store_id}/settings/cod-autopilot`

Request (all fields optional, validated bounds per data-model §4):
```json
{ "enabled": true, "digest_hour": 9, "assumed_delivered_days": 12 }
```
Response `200`: same shape as GET. Errors: `422` on bounds violation. Disabling takes effect immediately (FR-023): beat tasks re-read settings every run; pending sends are skipped, no rows deleted.

## 2. Exception queue

### `GET /api/v1/stores/{store_id}/orders/autopilot-exceptions`

Query: `limit` (default 50), `offset`.
Response `200`:
```json
{
  "items": [
    {
      "order_id": "…",
      "order_number": "#1042",
      "customer_name": "…",
      "total_cents": 45000,
      "currency": "EGP",
      "exception_reason": "refused",
      "flagged_at": "2026-07-18T10:00:00Z",
      "age_hours": 26,
      "attempts": 3,
      "order_status": "shipped"
    }
  ],
  "total": 1
}
```
Derived from `whatsapp_delivery_checks` where `outcome='exception'` (or `exception_reason='late_contradiction'`) and `exception_resolved_at IS NULL` (FR-021).

### `POST /api/v1/stores/{store_id}/orders/autopilot-exceptions/{order_id}/resolve`

Request: `{ "action": "dismiss" }` (v1: dismiss only — actual status changes go through existing order endpoints, which clear the flag as a side effect).
Response `200`: `{ "resolved": true }`. `404` if no unresolved exception for the order.

## 3. Existing endpoints — behavioral extensions (additive)

- `PATCH /stores/{store_id}/orders/{order_id}/status` and the status-changing use case accept an **optional** `source` on the DTO (internal callers only; not exposed in the public request schema in v1). Timeline entries (`GET .../orders/{order_id}/timeline`) MAY now include `"source"` in status-history items — additive field, no consumer breakage.
- Any terminal status change (delivered / returned / cancelled / refunded) on an order with an open `whatsapp_delivery_checks` row marks that row `superseded` and clears `next_attempt_at` (FR-018, FR-021 clearing rule).

## 4. Internal contracts (not HTTP)

### Beat tasks (`src/infrastructure/messaging/tasks/cod_autopilot_tasks.py`)

| Task name | Schedule | Contract |
|---|---|---|
| `tasks.cod_autopilot_send_digests` | hourly `crontab(minute=5)` | for each enabled store whose `digest_hour` == current store-local hour and with ≥1 eligible confirmed order and no digest row for today: build capped list, send `cod_ship_digest_v1` via guard, insert digest row. Idempotent via UNIQUE(store_id, digest_date) + message_log event_tag. |
| `tasks.cod_autopilot_delivery_checks` | hourly `crontab(minute=20)` | create check rows for newly-shipped eligible orders (delay elapsed); send due checks (`next_attempt_at <= now`, attempts < max) via guard; schedule retries; mark `response_exhausted` when attempts exhausted. |
| `tasks.cod_autopilot_assumed_delivered` | daily `crontab(hour=3, minute=30)` (after RTO sweep 03:00) | close `response_exhausted` rows past `assumed_delivered_due_at` whose order is still SHIPPED: stamp metadata, `UpdateOrderStatusUseCase(status=delivered, reason=autopilot_assumed_delivered, source=assumed_delivered)`; skip network delivery event (R-06). |

All three: `bind=True, max_retries=2, default_retry_delay=300`, module added to `celery_app.conf.imports`, RLS bypass for scan / `narrow_to_tenant` per write (clone `cod_auto_rto_task`).

### Webhook actions (Meta inbound, existing `POST /webhooks/whatsapp/callback`)

Button payload grammar (extends `parse_quick_reply_action`): `"<action>:<subdomain>/<uuid>"`.

| Payload | Handler contract |
|---|---|
| `shipall:<sub>/<digest_id>` | verify sender matches digest `merchant_phone` (digits-match); if `processed_at` set → ack "already handled"; else transition every `order_items` order still in CONFIRMED via bulk path with `source=merchant_digest`, skip moved ones (FR-005/FR-006 skip semantics), set `response_type=all_shipped`, `processed_at`, reply summary ("9 orders marked shipped, 1 skipped"). |
| `dlvyes:<sub>/<order_id>` | phone-match customer; if order SHIPPED and check not terminal → delivered via use case with `source=customer_confirmed` (network delivery event fires, full weight); outcome=`delivered_confirmed`; ack thank-you. Duplicate → ack only (FR-014). |
| `dlvnot:<sub>/<order_id>` | outcome stays pending; schedule next attempt if attempts remain, else `response_exhausted`; ack. |
| `dlvref:<sub>/<order_id>` | outcome=`exception`, reason=`refused`; no status/payment change (FR-013); ack; if order already terminal → flag `late_contradiction` only. |
| free text from merchant phone with open unexpired digest | exceptions grammar (R-04): parse → transition listed-minus-excepted with `source=merchant_digest`; unparseable → localized help + dashboard link, zero changes (FR-007). First response wins; later responses ack-only (FR-008). |

All handlers: wrapped try/except (webhook always 200), idempotent, resolve tenant via order/digest row then `narrow_to_tenant`.

### WhatsApp templates (Meta)

| Template | Category | Language | Variables | Buttons |
|---|---|---|---|---|
| `cod_ship_digest_v1` | UTILITY | en + ar | store_name, order_count, numbered_list (multi-line, ≤10 items), capped_note | 1 QUICK_REPLY "All shipped" |
| `order_delivery_check_v1` | UTILITY | en + ar | store_name, order_number | 3 QUICK_REPLY: Received / Not yet / Refused |

No emoji (Meta rule 2388060 precedent). Registered in `RICH_TEMPLATES`, seeded by data migration, submitted by `scripts/submit_platform_whatsapp_templates.py`. Sends blocked until status APPROVED (existing guard, FR-015).
