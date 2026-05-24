# Phase 1 — Data Model

**Feature**: WhatsApp Integration Phase 1 — Backend Foundation
**Date**: 2026-05-24

Three new tenant-scoped tables, one extension of an existing table, and one supporting customer-metadata key. All three new tables: tenant-scoped via `store_id`, RLS-protected via `app.current_store_id`, indexed for the dispatch / purge / lookup access patterns described in the spec.

---

## Table: `whatsapp_opt_ins`

Tracks per-(store, phone) consent state. **History-preserving**: re-opting after an opt-out creates a NEW row rather than mutating the prior row (FR-012).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID PK | `DEFAULT gen_random_uuid()` | |
| `store_id` | UUID NOT NULL | FK → `stores.id` ON DELETE CASCADE | Tenant scope; indexed |
| `customer_id` | UUID NULL | FK → `customers.id` ON DELETE SET NULL | Nullable: guest checkouts may not have a customer row yet |
| `phone` | VARCHAR(20) NOT NULL | E.164 format, validated by Pydantic + DB CHECK | `CHECK (phone ~ '^\+[1-9]\d{1,14}$')` |
| `source` | TEXT NOT NULL | ENUM-like (CHECK constraint) | `checkout`, `signup`, `import`, `api`, `inbound_reply` |
| `opted_in_at` | TIMESTAMPTZ NOT NULL | `DEFAULT NOW()` | |
| `opted_out_at` | TIMESTAMPTZ NULL | | NULL = currently opted in |
| `opt_out_reason` | TEXT NULL | | `inbound_stop_keyword`, `merchant_revoke`, `customer_request_via_support`, `api_revoke` |
| `created_at` | TIMESTAMPTZ NOT NULL | `DEFAULT NOW()` | |
| `updated_at` | TIMESTAMPTZ NOT NULL | `DEFAULT NOW()` | Trigger: update on row mutation |

**Indexes**:
- `ix_whatsapp_opt_ins_store_phone_active` → `(store_id, phone) WHERE opted_out_at IS NULL` — partial index, primary lookup path for the send guard ("does this phone have an active opt-in for this store?")
- `ix_whatsapp_opt_ins_store_phone_history` → `(store_id, phone, opted_in_at DESC)` — for DSAR exports and history views
- `ix_whatsapp_opt_ins_customer_id` → `(customer_id) WHERE customer_id IS NOT NULL`

**RLS**:
```sql
ALTER TABLE whatsapp_opt_ins ENABLE ROW LEVEL SECURITY;
CREATE POLICY whatsapp_opt_ins_tenant_isolation ON whatsapp_opt_ins
  USING (store_id = current_setting('app.current_store_id', true)::uuid);
```

**State transitions**:
- New row: `opted_in_at = NOW()`, `opted_out_at = NULL`, `opt_out_reason = NULL` → **active**.
- Update existing active row: `opted_out_at = NOW()`, `opt_out_reason = <reason>` → **revoked** (row stays for history).
- Re-opt-in after revoke: INSERT a fresh row (NEVER reset the prior row's `opted_out_at`).

**Guarantees**:
- Phone canonicalized to E.164 by the application layer before insert (FR-008).
- `customer_id` linked lazily — if a guest opt-in is later identified with a customer record, the existing customer-merge use-case updates the FK (FR-007).

**DSAR / Erasure**:
- Exported in the customer-data export keyed on `customer_id`.
- On `customers/redact` webhook: rows with that `customer_id` are deleted alongside other customer tables (CASCADE is via app logic, not DB cascade, to preserve store-level audit).

---

## Table: `whatsapp_scheduled_sends`

Future-dated intent to send a templated WhatsApp message. Scanned by the dispatcher Celery task every minute.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID PK | `DEFAULT gen_random_uuid()` | |
| `store_id` | UUID NOT NULL | FK → `stores.id` ON DELETE CASCADE | Tenant scope |
| `customer_id` | UUID NULL | FK → `customers.id` ON DELETE CASCADE | Cascade-delete: if customer is purged, kill the send |
| `phone` | VARCHAR(20) NOT NULL | E.164 | Denormalized for sends after customer purge in a grace window |
| `template_id` | UUID NULL | FK → `whatsapp_templates.id` ON DELETE RESTRICT | Either `template_id` OR `text_message` is required (CHECK) |
| `template_params` | JSONB NULL | | Positional/named parameters used at send-time |
| `text_message` | TEXT NULL | | Used only when sending within the 24h window without a template |
| `scheduled_for` | TIMESTAMPTZ NOT NULL | | Indexed |
| `status` | TEXT NOT NULL | `DEFAULT 'pending'`; CHECK in (`pending`, `sent`, `cancelled`, `skipped`, `failed`) | |
| `skip_reason` | TEXT NULL | | One of guard skip codes when `status='skipped'` |
| `failure_reason` | TEXT NULL | | When `status='failed'` |
| `related_order_id` | UUID NULL | FK → `orders.id` ON DELETE SET NULL | Set NULL on order delete; cancel-on-order-cancel is via app logic |
| `created_by` | UUID NULL | FK → `users.id` | Operator who scheduled; NULL = system-scheduled |
| `created_at` | TIMESTAMPTZ NOT NULL | `DEFAULT NOW()` | |
| `dispatched_at` | TIMESTAMPTZ NULL | | When the dispatcher took the row |
| `sent_at` | TIMESTAMPTZ NULL | | Final send timestamp |

**Indexes**:
- `ix_whatsapp_scheduled_sends_due` → `(scheduled_for) WHERE status = 'pending'` — primary dispatcher scan
- `ix_whatsapp_scheduled_sends_store` → `(store_id, scheduled_for)`
- `ix_whatsapp_scheduled_sends_order` → `(related_order_id) WHERE related_order_id IS NOT NULL` — cascade-cancel on order events

**CHECK constraints**:
- `CHECK ((template_id IS NOT NULL) OR (text_message IS NOT NULL))` — must have something to send.
- `CHECK (phone ~ '^\+[1-9]\d{1,14}$')`.

**RLS**: same pattern as opt-ins, by `store_id`.

**State machine**:
```
pending ──(dispatcher picks up + send succeeds)──→ sent
pending ──(user/system cancels)──────────────────→ cancelled
pending ──(guard rejects at dispatch-time)──────→ skipped
pending ──(retries exhausted on Meta error)─────→ failed (→ dead-letter row created)
```

**Idempotency at dispatch-time**: the dispatcher uses `SELECT ... FOR UPDATE SKIP LOCKED` so two workers cannot grab the same row.

---

## Table: `whatsapp_dead_letters`

Exhausted-retry record of a failed send. Read by operators; replayed manually; purged at 90 days (FR-035a).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | UUID PK | `DEFAULT gen_random_uuid()` | |
| `store_id` | UUID NOT NULL | FK → `stores.id` ON DELETE CASCADE | Tenant scope |
| `phone` | VARCHAR(20) NOT NULL | E.164 | |
| `customer_id` | UUID NULL | FK → `customers.id` ON DELETE SET NULL | |
| `template_id` | UUID NULL | FK → `whatsapp_templates.id` ON DELETE SET NULL | |
| `template_params` | JSONB NULL | | |
| `text_message` | TEXT NULL | | For free-form sends |
| `originating_context` | TEXT NOT NULL | CHECK in (`order_created`, `order_paid`, `order_status_changed`, `campaign`, `scheduled_send`, `abandoned_cart`, `ad_hoc`) | |
| `originating_context_id` | UUID NULL | | order_id, campaign_id, scheduled_send_id, etc. |
| `error_history` | JSONB NOT NULL | `DEFAULT '[]'::jsonb` | Array of `{attempt_n, at, http_status, meta_error_code, error_message}` |
| `error_classification` | TEXT NOT NULL | CHECK in (`retriable_exhausted`, `non_retriable`) | |
| `final_error_code` | TEXT NULL | | Meta's `error.code` if available |
| `replay_state` | TEXT NOT NULL | `DEFAULT 'not_replayed'`; CHECK in (`not_replayed`, `replaying`, `replayed_success`, `replayed_failed`) | |
| `replayed_at` | TIMESTAMPTZ NULL | | |
| `replayed_by` | UUID NULL | FK → `users.id` | |
| `replayed_send_id` | UUID NULL | FK → `message_log.id` ON DELETE SET NULL | The new message_log row from the replay attempt |
| `created_at` | TIMESTAMPTZ NOT NULL | `DEFAULT NOW()` | Drives 90-day purge |

**Indexes**:
- `ix_whatsapp_dead_letters_store_created` → `(store_id, created_at DESC)` — list view
- `ix_whatsapp_dead_letters_purge` → `(created_at)` — purge task scan
- `ix_whatsapp_dead_letters_context` → `(originating_context, originating_context_id)` — find dead-letters for a specific order/campaign

**RLS**: same pattern by `store_id`.

**Replay double-send guard** (FR-035): before issuing a replay, the use-case queries `message_log` for any send with the same `(store_id, phone, template_name, metadata->>'idempotency_key')` and status in `{sent, delivered, read}`. If found, replay marks the row `replayed_success` without re-sending.

---

## Extension: `whatsapp_templates` (existing table)

Two changes:

1. **`is_system` field already exists** — populate it for the seeded canonical templates listed in FR-030 (`order_confirmation`, `payment_received`, `order_shipped`, `order_delivered`, `abandoned_cart`). Seeded in the same Alembic migration; one row per (template_name, language=`en`/`ar`).
2. **Category-driven send-guard logic** uses the existing `category` column (`UTILITY`, `MARKETING`, `AUTHENTICATION`). No schema change; the guard reads this and applies the two-tier rule (FR-011).

---

## Extension: `customers.metadata['whatsapp_prefs']`

JSONB nested dict on the existing `customers.metadata`. No schema change required. Phase 1 reads these keys (all default `True`):
- `whatsapp_prefs.order_confirmation`
- `whatsapp_prefs.payment_received`
- `whatsapp_prefs.shipping_update` (existing)
- `whatsapp_prefs.delivery_confirmation` (existing)
- `whatsapp_prefs.abandoned_cart` (existing)

The merchant-hub UI to set these is Phase 4; the backend reads them now with safe defaults.

---

## Extension: `store_settings.whatsapp_notifications`

JSONB nested dict on the existing per-store settings. Phase 1 introduces two new keys:
- `store_settings.whatsapp_notifications.order_confirmation` (boolean)
- `store_settings.whatsapp_notifications.payment_received` (boolean)

Default value per FR-019a:
- New store created in `platform_managed` mode → defaults `True` for all notification keys.
- Store transitions to `byo` mode → all `whatsapp_notifications.*` keys reset to `False`.
- Store transitions back to `platform_managed` → restore from the snapshot taken at last platform-managed state; if none, default `True`.

A backup snapshot is stored in `store_settings.whatsapp_notifications_prev_platform_managed` (same shape).

---

## Alembic migration: `20260524_add_whatsapp_optin_scheduled_dl.py`

**Up**:
1. CREATE TABLE `whatsapp_opt_ins` + RLS + policy + indexes.
2. CREATE TABLE `whatsapp_scheduled_sends` + RLS + policy + indexes.
3. CREATE TABLE `whatsapp_dead_letters` + RLS + policy + indexes.
4. INSERT system templates into `whatsapp_templates` (`is_system=true`) — one row per (name, language) for the five canonical templates × `en` + `ar`.
5. CREATE GIN index on `message_log.metadata` if not exists (R5).
6. Defensively add `whatsapp_notifications` JSONB nesting to `store_settings` if not already present.

**Down**: drop in reverse order. `down()` is local-rollback only per constitution (Alembic discipline).

---

## Entity relationships (text diagram)

```
stores ──┬──< whatsapp_opt_ins >── customers (nullable)
         ├──< whatsapp_scheduled_sends >── customers (nullable), whatsapp_templates (nullable), orders (nullable)
         ├──< whatsapp_dead_letters >── customers (nullable), whatsapp_templates (nullable), message_log (replay)
         ├──< whatsapp_templates  (existing — extended w/ system seeds)
         ├──< whatsapp_conversations  (existing — unchanged)
         ├──< whatsapp_campaigns  (existing — unchanged)
         ├──< whatsapp_campaign_recipients  (existing — unchanged)
         └──< message_log  (existing — used for idempotency + replay guard)

service_credentials (existing) ──→ store (BYO Meta credentials; encrypted)
```

---

## Validation rules summary

- Every phone written to any of the three new tables must be E.164 (Pydantic + DB CHECK).
- `whatsapp_scheduled_sends`: exactly one of `template_id`/`text_message` must be non-null.
- `whatsapp_opt_ins`: re-opt creates a new row, never mutates an old one.
- `whatsapp_dead_letters.replay_state` follows the FSM above; no skipping.
- All three tables have RLS that filters by `store_id = current_setting('app.current_store_id', true)::uuid`.

---

## What does NOT change

- `whatsapp_templates` columns and constraints (only data seeding + interpretation by the guard).
- `whatsapp_conversations`, `whatsapp_campaigns`, `whatsapp_campaign_recipients`, `message_log` schemas.
- `service_credentials` schema or encryption mechanism.
- Existing send_* method signatures on `WhatsAppMessagingService` (the guard wraps them; FR-041).
- Existing `/webhooks/whatsapp/callback` route URL (FR-040 — backward compatibility).
