# Phase 1 — Data Model

Two new tables + one column addition on an existing table. All new tables are tenant-scoped with RLS policies enabled in the same Alembic migration.

## New entity 1 — `campaign_auto_match_rules`

Per-store auto-attribution rules for incoming funnel events. Evaluated at ingest BEFORE the existing short_code-based lookup; first match wins (`ORDER BY priority ASC`).

| Column | Type | Constraints | Purpose |
| ------ | ---- | ----------- | ------- |
| `id` | UUID | PK, default `gen_random_uuid()` | Stable rule identifier |
| `tenant_id` | UUID | NOT NULL | RLS scoping; matches the campaign's tenant |
| `store_id` | UUID | NOT NULL, FK `stores.id` ON DELETE CASCADE | Store scope (rules apply only to this store's traffic) |
| `campaign_id` | UUID | NOT NULL, FK `marketing_campaigns.id` ON DELETE CASCADE | The campaign that gets stamped on matching events |
| `group_id` | UUID | NOT NULL | Logical group key — rows sharing a `group_id` are a single multi-condition rule |
| `combinator` | TEXT | NOT NULL, CHECK IN (`AND`, `OR`) | How rows in the same group are combined |
| `field` | TEXT | NOT NULL, CHECK IN (`utm_source`, `utm_medium`, `utm_campaign`) | Which UTM dimension to match |
| `operator` | TEXT | NOT NULL, CHECK IN (`equals`, `starts_with`, `contains`) | Match semantics |
| `value` | TEXT | NOT NULL, length ≤ 200 | The value to match against (sanitized at ingest the same way UTMs are) |
| `priority` | INTEGER | NOT NULL | Store-global precedence — lower wins. Unique-per-store constraint enforces no ties. |
| `created_at` | TIMESTAMPTZ | NOT NULL, default `now()` | Audit trail |
| `created_by` | UUID | NOT NULL, FK `users.id` | Who created the rule |

**Indexes**:
- `ix_campaign_auto_match_rules_store_priority` on `(store_id, priority)` — drives the ingest-time ordered fetch
- `ix_campaign_auto_match_rules_campaign_id` on `(campaign_id)` — drives the campaign-scoped CRUD reads
- Unique constraint `uq_campaign_auto_match_rules_store_priority` on `(store_id, priority)` — enforces unambiguous precedence

**RLS policy** (in same migration):
```sql
ALTER TABLE campaign_auto_match_rules ENABLE ROW LEVEL SECURITY;
CREATE POLICY campaign_auto_match_rules_tenant_isolation
  ON campaign_auto_match_rules
  FOR ALL TO PUBLIC
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

**State transitions**: rules are stateless — no enum for status. A rule is either present (active) or deleted (CASCADE on campaign delete). To disable temporarily, the merchant deletes and recreates; no soft-disable in v1.

## New entity 2 — `campaign_activities`

Audit log of merchant-initiated campaign actions. Initially captures only `backfill_attribution` (US5); extensible via the `type` enum for future activity types (e.g., `recompute_attribution`, `rerun_audience_filter`).

| Column | Type | Constraints | Purpose |
| ------ | ---- | ----------- | ------- |
| `id` | UUID | PK, default `gen_random_uuid()` | |
| `tenant_id` | UUID | NOT NULL | RLS scoping |
| `store_id` | UUID | NOT NULL, FK `stores.id` ON DELETE CASCADE | |
| `campaign_id` | UUID | NOT NULL, FK `marketing_campaigns.id` ON DELETE CASCADE | |
| `type` | TEXT | NOT NULL, CHECK IN (`backfill_attribution`) | Extensible enum for future activity types |
| `status` | TEXT | NOT NULL, default `running`, CHECK IN (`running`, `completed`, `failed`) | State machine for async work |
| `payload` | JSONB | NOT NULL | Activity-specific snapshot — for backfills: `{ utm_filters: [...], starts_at, ends_at }` |
| `affected_count` | INTEGER | NULL until status=completed | Number of rows updated |
| `skipped_count` | INTEGER | NULL until status=completed | Number of rows already attributed (FR-025 skip) |
| `error_message` | TEXT | NULL | Populated when status=failed |
| `run_at` | TIMESTAMPTZ | NOT NULL, default `now()` | When the activity started |
| `completed_at` | TIMESTAMPTZ | NULL until status terminal | |
| `run_by` | UUID | NOT NULL, FK `users.id` | Audit trail |

**Indexes**:
- `ix_campaign_activities_campaign_run_at` on `(campaign_id, run_at DESC)` — drives the activities log list (most recent first)
- `ix_campaign_activities_store_status` on `(store_id, status)` partial WHERE `status = 'running'` — quick check for in-progress backfills

**RLS policy** (in same migration):
```sql
ALTER TABLE campaign_activities ENABLE ROW LEVEL SECURITY;
CREATE POLICY campaign_activities_tenant_isolation
  ON campaign_activities
  FOR ALL TO PUBLIC
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

**State transitions**: `running` → `completed` | `failed`. Celery task transitions on completion. Status updates use SAVEPOINT to avoid losing the audit row on partial failure.

## Column addition — `funnel_events.device`

Adds one nullable TEXT column for device classification (FR-016). Backfilled rows stay NULL.

| Column | Type | Constraints | Purpose |
| ------ | ---- | ----------- | ------- |
| `device` | TEXT | NULL, CHECK IN (`mobile`, `tablet`, `desktop`) OR NULL | UA-derived device class; NULL means unparsed/historical |

**Index**:
- `ix_funnel_events_store_campaign_device` on `(store_id, campaign_id, device)` partial WHERE `device IS NOT NULL AND campaign_id IS NOT NULL` — drives the "Sessions by device" panel for a campaign

**Migration**: column is nullable, no default, no backfill of historical rows. Ingest service starts populating from migration deployment time forward. Historical rows in the device panel show "Unknown" bucket.

## Reused entities (no schema changes)

| Entity | Why referenced | Touch point |
| ------ | -------------- | ----------- |
| `marketing_campaigns` | Parent FK for rules + activities; source of duplicate scope | FR-029 reads name/channel/subject/body/audience_filter/segment_id/template_id to copy |
| `orders` | Backfill writes `campaign_id`; comparison + KPI queries read it | FR-024 sets `campaign_id`; analytics aggregations group by it |
| `funnel_events` | Backfill writes `campaign_id`; auto-match rules write `campaign_id` at ingest; device panel reads new `device` column | FR-018, FR-024 |
| `customers` | New-vs-returning panel reads `first_touch_at`, `first_touch_attribution` JSONB | FR-011 new-vs-returning donut |
| `coupons` | Coupon cannibalization tip reads coupon redemption stats joined to orders | FR-038 |
| `customer_touches` | Multi-touch attribution model selector on the detail page reads this | FR-013 |
| `marketing_campaign_sends` | Best-time-to-send picker reads weekday × hour aggregates | FR-040 |

## Entity-relationship sketch

```
stores
   1
   │
   ├──── marketing_campaigns ── 1 ── n ── campaign_auto_match_rules (NEW)
   │       │                            (grouped by group_id)
   │       │
   │       ├── 1 ── n ── campaign_activities (NEW)
   │       │
   │       ├── 1 ── n ── orders (existing — campaign_id FK)
   │       │
   │       ├── 1 ── n ── funnel_events (existing — campaign_id FK; new device column)
   │       │
   │       ├── 1 ── n ── coupons (existing — campaign_id FK)
   │       │
   │       └── 1 ── n ── marketing_campaign_sends (existing — per-recipient send log)
   │
   └──── customers (existing — first_touch_attribution feeds new-vs-returning)
```

## Migration plan

| Order | File | Purpose | Down semantics |
| ----- | ---- | ------- | -------------- |
| 1 | `20260524_010000_add_campaign_auto_match_rules.py` | Creates table + indexes + RLS policy | Drops table + policy |
| 2 | `20260524_020000_add_campaign_activities.py` | Creates table + indexes + RLS policy | Drops table + policy |
| 3 | `20260524_030000_add_funnel_events_device.py` | Adds nullable `device` column + partial index | Drops index + column |

All three are additive (no data loss possible on `down`). They land in the same PR so the merchant-hub frontend can light up after the migration sequence completes on the test env. No data backfill needed for the column add — historical rows show "Unknown" bucket in the device panel.
