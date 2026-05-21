# Data Model — UTM & Campaign Attribution

**Feature**: 001-utm-campaign-attribution
**Phase**: 1 (Design & Contracts)
**Date**: 2026-05-21

This file specifies every persistent change. Each section names the affected entity, the columns being added, the indexes, the FK behavior, and the migration ordering.

---

## Entity 1 — `marketing_campaigns` (extend)

Existing model: `src/infrastructure/database/models/tenant/marketing_campaign.py`.

### New columns

| Column        | Type         | Nullable | Default | Notes                                                                  |
| ------------- | ------------ | -------- | ------- | ---------------------------------------------------------------------- |
| `short_code`  | `VARCHAR(8)` | NO       | —       | Crockford base32, generated server-side at create time. Unique per store. |

### New indexes

- `UNIQUE (store_id, short_code)` — partial index `WHERE short_code IS NOT NULL` only needed during the backfill window; after migration completes (NOT NULL), make it a plain unique constraint.

### Migration notes

- Backfill: existing campaigns get a generated `short_code` during migration (same generator the service uses, run inside the migration's `upgrade()`). After backfill, the column flips to `NOT NULL`.
- Conflict resolution during backfill: deterministic seeded RNG keyed on `(store_id, campaign_id)` so re-running the migration yields the same codes.

### Entity-level rationale

Campaigns are renamed by merchants. The UTM-value the customer sees in their URL must not change when that happens. `short_code` is a stable, opaque identifier the link generator embeds in `utm_campaign`. Resolution at checkout/funnel-event ingest matches on `short_code` first, then falls back to a fuzzy match on lower(trim(name)) for legacy campaigns (one release window only — remove the fallback after backfill confirmed).

---

## Entity 2 — `orders` (extend)

Existing model: `src/infrastructure/database/models/tenant/order.py` (UTM region at lines 144–156).

### New columns

| Column          | Type           | Nullable | Default | Notes                                                                 |
| --------------- | -------------- | -------- | ------- | --------------------------------------------------------------------- |
| `utm_term`      | `VARCHAR(200)` | YES      | NULL    | Standard UTM dimension (paid-search keyword historically; merchants use it freely). |
| `utm_content`   | `VARCHAR(200)` | YES      | NULL    | Standard UTM dimension (ad creative variant historically).             |
| `campaign_id`   | `UUID`         | YES      | NULL    | FK → `marketing_campaigns.id` ON DELETE SET NULL. Resolved at checkout from `utm_campaign` short_code. |
| `attribution`   | `JSONB`        | YES      | NULL    | Full attribution snapshot: `{ first_touch: {...}, last_touch: {...}, session_id }`. Shape mirrors the `numu_attribution` cookie. |
| `first_touch_at`| `TIMESTAMPTZ`  | YES      | NULL    | Convenience column for "how long between first contact and purchase" analytics — derived from `attribution.first_touch.ts` on insert. |

### New indexes

- `ix_orders_store_campaign_created (store_id, campaign_id, created_at)` partial `WHERE campaign_id IS NOT NULL`. Powers campaign-performance queries.

### Migration notes

- Pure additive change. No backfill needed; existing orders simply have `NULL` for these new fields.
- Existing `utm_source`, `utm_medium`, `utm_campaign` columns are unchanged and remain authoritative for the raw UTM strings.

---

## Entity 3 — `funnel_events` (extend)

Existing model: `src/infrastructure/database/models/tenant/funnel_event.py`.

### New columns

| Column          | Type           | Nullable | Default | Notes                                                  |
| --------------- | -------------- | -------- | ------- | ------------------------------------------------------ |
| `utm_source`    | `VARCHAR(200)` | YES      | NULL    | Mirrors order column; populated server-side from cookie on event ingest. |
| `utm_medium`    | `VARCHAR(200)` | YES      | NULL    |                                                        |
| `utm_campaign`  | `VARCHAR(200)` | YES      | NULL    |                                                        |
| `utm_term`      | `VARCHAR(200)` | YES      | NULL    |                                                        |
| `utm_content`   | `VARCHAR(200)` | YES      | NULL    |                                                        |
| `campaign_id`   | `UUID`         | YES      | NULL    | FK → `marketing_campaigns.id` ON DELETE SET NULL.       |
| `referrer`      | `VARCHAR(500)` | YES      | NULL    | Top-level promotion of what is currently inside `step_data.referrer`. |

### New indexes

- `ix_funnel_events_store_campaign_created (store_id, campaign_id, created_at)` partial `WHERE campaign_id IS NOT NULL`.
- `ix_funnel_events_store_utm_campaign (store_id, utm_campaign, created_at)` partial `WHERE utm_campaign IS NOT NULL`. Powers per-campaign funnel breakdowns even for unresolved/external campaigns.

### Migration notes

- Pure additive change. No backfill — existing rows stay null for attribution columns.
- `step_data` continues to hold path, custom event payload, and anything not promoted to a column. Server code stops writing `referrer` into `step_data` (column is the new home); reader code falls back to `step_data.referrer` for historical rows.

---

## Entity 4 — `customers` (extend)

Existing model: `src/infrastructure/database/models/tenant/customer.py`.

### New columns

| Column                     | Type    | Nullable | Default | Notes                                                                |
| -------------------------- | ------- | -------- | ------- | -------------------------------------------------------------------- |
| `first_touch_attribution`  | `JSONB` | YES      | NULL    | The very first attribution snapshot ever associated with this customer (set once, never overwritten). Same shape as `orders.attribution.first_touch`. |
| `first_touch_at`           | `TIMESTAMPTZ` | YES | NULL    | Timestamp of the first-touch event.                                  |

### Migration notes

- Set on first attributed order (the order-creation service writes this if the column is null).
- Backfill is *optional*: a one-shot script can populate from each customer's earliest order's `attribution.first_touch`; if skipped, `first_touch_attribution` simply starts being populated from new-customer orders going forward. The spec does not require backfill (acceptance scenarios cover forward behavior).

---

## Entity 5 — `coupons`

**No change in v1.** The `campaign_id` FK on coupons is deferred to v2 per the spec's non-goals.

---

## New entity — *(none)*

No new tables. All changes are additive columns on existing entities. This keeps the migration set small and reversible.

---

## Migration plan

Single forward migration to keep the operational surface tight:

**File**: `alembic/versions/utm_attribution_20260521_add_campaign_attribution.py`
**Down revision**: whatever the current head is at implementation time
**Description**: "Add campaign attribution: short_code, utm_term/content, campaign_id FK, attribution JSONB, customer first-touch"

Operations in order:

1. `ALTER TABLE marketing_campaigns ADD COLUMN short_code VARCHAR(8)` (nullable initially)
2. Run backfill — populate `short_code` for every existing row via deterministic generator. Verify zero NULLs.
3. `ALTER TABLE marketing_campaigns ALTER COLUMN short_code SET NOT NULL`
4. `CREATE UNIQUE INDEX uq_campaigns_store_short_code ON marketing_campaigns (store_id, short_code)`
5. `ALTER TABLE orders ADD COLUMN utm_term VARCHAR(200), ADD COLUMN utm_content VARCHAR(200), ADD COLUMN campaign_id UUID, ADD COLUMN attribution JSONB, ADD COLUMN first_touch_at TIMESTAMPTZ`
6. `ALTER TABLE orders ADD CONSTRAINT fk_orders_campaign_id FOREIGN KEY (campaign_id) REFERENCES marketing_campaigns(id) ON DELETE SET NULL`
7. `CREATE INDEX ix_orders_store_campaign_created ON orders (store_id, campaign_id, created_at) WHERE campaign_id IS NOT NULL`
8. `ALTER TABLE funnel_events ADD COLUMN utm_source VARCHAR(200), ADD COLUMN utm_medium VARCHAR(200), ADD COLUMN utm_campaign VARCHAR(200), ADD COLUMN utm_term VARCHAR(200), ADD COLUMN utm_content VARCHAR(200), ADD COLUMN campaign_id UUID, ADD COLUMN referrer VARCHAR(500)`
9. `ALTER TABLE funnel_events ADD CONSTRAINT fk_funnel_events_campaign_id FOREIGN KEY (campaign_id) REFERENCES marketing_campaigns(id) ON DELETE SET NULL`
10. `CREATE INDEX ix_funnel_events_store_campaign_created ON funnel_events (store_id, campaign_id, created_at) WHERE campaign_id IS NOT NULL`
11. `CREATE INDEX ix_funnel_events_store_utm_campaign ON funnel_events (store_id, utm_campaign, created_at) WHERE utm_campaign IS NOT NULL`
12. `ALTER TABLE customers ADD COLUMN first_touch_attribution JSONB, ADD COLUMN first_touch_at TIMESTAMPTZ`

`downgrade()` reverses in reverse order, dropping indexes/FKs first.

### Migration safety

- **Locking**: All `ALTER TABLE ADD COLUMN` for nullable columns are O(1) metadata-only operations on Postgres 11+. No table rewrite. Safe on production tables of any size.
- **FK addition** (`fk_orders_campaign_id`, `fk_funnel_events_campaign_id`) — Postgres acquires `SHARE ROW EXCLUSIVE` on both ends. Done with `NOT VALID` first, then `VALIDATE CONSTRAINT` in a follow-up migration if either table is hot enough to matter; for current NUMU scale, single-shot is fine.
- **Backfill of `short_code`** runs inside the migration. Deterministic-seeded so re-running is idempotent (same code for same campaign).
- **Index creation** uses `CONCURRENTLY` (out of migration, in a follow-up `ALTER TABLE` script that doesn't run inside a transaction — Alembic supports this via `op.execute("CREATE INDEX CONCURRENTLY …")` with `transaction_per_migration=False`). Done this way to avoid taking write locks on `orders` / `funnel_events`.

---

## Attribution snapshot JSON schema

Stored on both `orders.attribution` (full snapshot) and `customers.first_touch_attribution` (first_touch leaf only). Same shape as the `numu_attribution` cookie:

```json
{
  "v": 1,
  "first_touch": {
    "ts": "2026-05-21T14:33:00Z",
    "utm_source": "facebook",
    "utm_medium": "social",
    "utm_campaign": "eid-sale-2026-AB7K",
    "utm_term": null,
    "utm_content": null,
    "gclid": null,
    "fbclid": "PAQ...",
    "referrer": "https://www.facebook.com/",
    "landing_path": "/product/abc-123"
  },
  "last_touch": { /* same shape as first_touch */ },
  "session_id": "01HX2M..."
}
```

Constraints:
- All fields except `ts` are nullable.
- Top-level `v` is the schema version. v1 is fixed by this feature; bumping requires a migration of stored payloads.
- `customers.first_touch_attribution` stores only the leaf shape (the `first_touch` object), not the whole envelope.
- `ts` is ISO-8601 UTC.

---

## Resolution flow at checkout (concrete)

When the storefront submits a `CheckoutRequest` carrying `attribution`:

1. Read the request body's `attribution.last_touch.utm_campaign` (the campaign string).
2. If non-null, query `SELECT id FROM marketing_campaigns WHERE store_id = :store_id AND (short_code = :stripped_code OR lower(trim(name)) = lower(trim(:utm_campaign_full))) LIMIT 1`. The split: campaign strings produced by the link builder are `<slug>-<short_code>`; the lookup strips the trailing `-<short_code>` block.
3. If a campaign matches: stamp `orders.campaign_id` with that UUID.
4. If no match: leave `campaign_id` NULL (per FR-011, unknown campaign strings keep raw UTMs but no FK).
5. Always stamp `orders.utm_source`, `orders.utm_medium`, `orders.utm_campaign`, `orders.utm_term`, `orders.utm_content` from the request body (raw values, sanitized).
6. Always stamp `orders.attribution` with the full `attribution` JSON from the request body.
7. Compute `orders.first_touch_at` from `attribution.first_touch.ts`.
8. If `customers.first_touch_attribution` is NULL, write the `attribution.first_touch` leaf to it and set `customers.first_touch_at`.

---

## Funnel event ingest flow (concrete)

When the storefront posts to `/storefront/store/{store_id}/track` or `/track-event`:

1. Read `numu_attribution` from request cookies (server-side, since this is a server-side endpoint).
2. Parse JSON, validate via Pydantic.
3. Pass `attribution.last_touch` fields to `_emit_funnel_event` via new kwargs.
4. Inside the sync write or the Celery task: stamp `funnel_events.utm_source/medium/campaign/term/content` from `attribution.last_touch`, set `funnel_events.referrer` from `attribution.last_touch.referrer`.
5. Resolve `campaign_id` the same way as in checkout (lookup by short_code or name). Stamp the FK column.
6. If parsing fails or the cookie is absent, write the funnel event with NULL attribution columns. Never block the response.

---

## Backwards compatibility

- `orders.utm_source/medium/campaign` retain their old behavior. Existing reports (`traffic_sources`) continue to work without change.
- `funnel_events.step_data` continues to be written for everything that doesn't have a dedicated column. Old rows are untouched.
- `MarketingCampaignModel` is extended (not replaced); existing routes and the WhatsAppCampaigns UI continue functioning.
