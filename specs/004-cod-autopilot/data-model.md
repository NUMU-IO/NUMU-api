# Data Model: COD Autopilot (004-cod-autopilot)

All new tables are tenant-scoped with full RLS (Constitution V), created in one Alembic migration using the `_add_rls_for_table` pattern from `alembic/versions/20260524_010000_add_whatsapp_optin_scheduled_dl.py:148` (ENABLE + FORCE RLS, 4 `tenant_isolation_*` policies on `public.get_current_tenant_id()`, `admin_bypass` on `public.is_rls_bypassed()`).

## 1. `whatsapp_delivery_checks` (new table)

One row per Autopilot-eligible order, created when the order enters `shipped`. Drives the customer delivery-check conversation, retries, fallback timing, and the exception queue.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID, NOT NULL, indexed | RLS key |
| `store_id` | UUID, NOT NULL, FK stores | |
| `order_id` | UUID, NOT NULL, **UNIQUE**, FK orders | one check lifecycle per order |
| `customer_phone` | String(20), NOT NULL | E.164; tenant-scoped (raw PII allowed here, never in network scope) |
| `attempts` | Integer, default 0 | delivery-check messages sent |
| `max_attempts` | Integer, default 3 | frozen from settings at row creation |
| `next_attempt_at` | DateTime(tz), nullable, indexed | when the next send is due; NULL = no send pending |
| `first_sent_at` / `last_sent_at` | DateTime(tz), nullable | |
| `response` | String(20), default `'none'` | `none / received / not_yet / refused` (last response) |
| `responded_at` | DateTime(tz), nullable | |
| `outcome` | String(30), default `'pending'`, indexed | see state machine below |
| `exception_reason` | String(30), nullable | `refused / response_exhausted / late_contradiction` |
| `exception_resolved_at` | DateTime(tz), nullable | set by resolve/dismiss or by any terminal order action |
| `assumed_delivered_due_at` | DateTime(tz), NOT NULL, indexed | `shipped_at + assumed_delivered_days` frozen at creation |
| `created_at` / `updated_at` | DateTime(tz) | |

**`outcome` state machine** (single writer per transition; all writers re-check current value — idempotent):

```
pending ──customer taps Received──────────────→ delivered_confirmed   (terminal)
pending ──customer taps Refused───────────────→ exception             (reason=refused)
pending ──attempts exhausted, no response─────→ response_exhausted    (still sweepable)
response_exhausted ──fallback due elapses─────→ assumed_delivered     (terminal)
response_exhausted ──late Received tap────────→ delivered_confirmed   (terminal, FR-017)
response_exhausted ──late Refused tap─────────→ exception             (reason=refused)
pending|response_exhausted ──order left SHIPPED by other path (RTO/cancel/refund/manual)──→ superseded (terminal, FR-018)
exception ──merchant resolves/dismisses───────→ (exception_resolved_at set; row terminal)
any terminal ──late contradicting Refused tap─→ exception_reason=late_contradiction flag only (order untouched)
```

**Validation rules**: `attempts <= max_attempts`; `next_attempt_at` NULL whenever outcome is terminal; a row is created only for COD orders with no integrated-courier shipment (FR-001) at stores with `cod_autopilot.enabled`.

## 2. `whatsapp_ship_digests` (new table)

One row per store per local day, created when the digest is sent.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID, NOT NULL, indexed | RLS key |
| `store_id` | UUID, NOT NULL, FK stores | |
| `digest_date` | Date, NOT NULL | store-local date; **UNIQUE (store_id, digest_date)** — at most one digest/day (FR-003) |
| `sent_at` | DateTime(tz), NOT NULL | |
| `message_id` | String(255), nullable | Meta message id of the sent digest |
| `merchant_phone` | String(20), NOT NULL | snapshot of `store.contact_phone` at send time; inbound replies matched against this |
| `order_items` | JSONB, NOT NULL | `[{"n": 1, "order_id": "...", "order_number": "#1042"}, ...]` — the ONLY orders a response may act on (FR-005/FR-009); max 10 (R-09) |
| `capped_count` | Integer, default 0 | eligible orders not listed (FR-009 transparency) |
| `response_type` | String(20), default `'none'` | `none / all_shipped / exceptions` |
| `response_raw` | Text, nullable | verbatim merchant reply (audit) |
| `excepted_numbers` | JSONB, nullable | parsed item numbers held back |
| `processed_at` | DateTime(tz), nullable | **set exactly once** — presence = digest consumed (FR-008); duplicate/late responses acknowledged without action |
| `expires_at` | DateTime(tz), NOT NULL | `sent_at + 48h`; free-text replies after this are ignored (R-03) |
| `created_at` / `updated_at` | DateTime(tz) | |

## 3. `orders` — extended, no schema change

- `metadata["status_history"][]` entries gain optional key `"source"`: `customer_confirmed / merchant_digest / assumed_delivered` (absent = manual, R-05).
- New metadata flags: `autopilot_assumed_delivered_at` (pre-transition idempotency stamp, R-07), `network_delivery_skipped` (R-06 audit).
- Existing behavior reused unchanged: `transition_to(DELIVERED)` auto-sets `payment_status=PAID` for COD; `VALID_STATUS_TRANSITIONS` untouched.

## 4. `store.settings["cod_autopilot"]` (new JSONB section)

```json
{
  "enabled": false,
  "digest_hour": 18,
  "delivery_check_delay_days": 3,
  "delivery_check_retry_days": 2,
  "delivery_check_max_attempts": 3,
  "assumed_delivered_days": 10
}
```

Pydantic schemas (`CodAutopilotResponse` / `UpdateCodAutopilotRequest`) in `src/api/v1/schemas/tenant/settings.py`, bounds: `digest_hour` 0–23, `delivery_check_delay_days` 1–7, `delivery_check_retry_days` 1–7, `delivery_check_max_attempts` 1–3, `assumed_delivered_days` 5–30. Response additionally surfaces read-only `digest_deliverable: bool` (store has usable contact_phone) and `auto_rto_days` (for UI overlap warning, R-12).

## 5. `whatsapp_templates` — new seeded rows (existing table)

Registry additions to `RICH_TEMPLATES` + seed migration + Meta submission (R-08):

| name | audience | category | buttons (QUICK_REPLY payload prefix) |
|---|---|---|---|
| `cod_ship_digest_v1` (en/ar) | merchant | UTILITY | All shipped → `shipall:` |
| `order_delivery_check_v1` (en/ar) | customer | UTILITY | Received → `dlvyes:` · Not yet → `dlvnot:` · Refused → `dlvref:` |

## 6. `message_logs` — existing table, new event tags

Idempotency keys (R-10): digest sends `event_tag="digest:{digest_date}"`; delivery checks `event_tag="dlvcheck:{order_id}:{attempt}"`. No schema change.

## 7. `network_reputation` — NO changes

Delivery events fire only for `customer_confirmed` (and manual) delivered transitions; `assumed_delivered` closures are excluded (R-06). RTO path unchanged.

## Relationships

```
stores 1 ──── * whatsapp_ship_digests (order_items JSONB → orders by id)
orders 1 ──── 1 whatsapp_delivery_checks (UNIQUE order_id)
whatsapp_delivery_checks.outcome ──derives──→ exception queue view
store.settings.cod_autopilot ──governs──→ both beat tasks + row creation
```
