---
description: "Tasks for backend-001 billing-sync endpoint"
---

# Tasks: backend-001 — Billing Sync

**Input**: [spec.md](./spec.md), [plan.md](./plan.md)

## Format: `[ID] [P?] Description`

`[P]` = parallel-safe.

---

## Phase 1: Setup (entity + schema)

- [ ] T001 Append `ShopifySubscription` entity to `src/core/entities/shopify.py`.
- [ ] T002 [P] Append `BillingSyncRequest`, `BillingSubscriptionResponse`,
      `PlanIdLiteral`, `SubscriptionStatusLiteral` Pydantic schemas to
      `src/api/v1/schemas/shopify.py`.

## Phase 2: Persistence (model + migration + repo)

- [ ] T003 Create `src/infrastructure/database/models/tenant/shopify_subscription.py`
      with `ShopifySubscriptionModel`. Composite unique on
      `(store_id, shopify_subscription_id)`. Indexes on `store_id`,
      `tenant_id`, `synced_at`.
- [ ] T004 Create `alembic/versions/20260508_add_shopify_subscriptions.py`.
      `up()` creates the table, indexes, unique constraint, and enables
      RLS with a policy filtering by `tenant_id` from the request
      context. `down()` drops in reverse order.
- [ ] T005 Append `ShopifySubscriptionRepository` to
      `src/infrastructure/repositories/shopify_repository.py`. Methods:
      `upsert(store_id, body)`, `get_active(store_id)`,
      `get_by_subscription_id(store_id, sub_id)`. Uses Postgres
      `INSERT ... ON CONFLICT ... DO UPDATE` for idempotent upsert.

## Phase 3: API surface

- [ ] T006 Add `get_shopify_subscription_repo` factory to
      `src/api/dependencies/shopify.py`.
- [ ] T007 Create `src/application/use_cases/shopify/billing_sync.py`:
      `BillingSyncUseCase` that takes `(store_id, request)` → calls
      repo upsert → returns entity. Sets `cancelled_at` when status
      transitions to CANCELLED/EXPIRED/FROZEN; preserves earliest
      timestamp on retries.
- [ ] T008 Create `src/api/v1/routes/shopify/billing.py` with two
      endpoints:
      - `POST /{store_id}/billing/sync` — upsert
      - `GET /{store_id}/billing/subscription` — read latest active
      Both use `verify_internal_key` dependency.
- [ ] T009 Mount the new `billing_router` in
      `src/api/v1/routes/shopify/__init__.py` with tag
      `Shopify - Billing`.

## Phase 4: Tests

- [ ] T010 Create `tests/api/test_shopify_billing.py` covering:
      - 403 on missing `X-Internal-Key`
      - 422 on invalid `plan_id` (e.g. `"enterprise"`)
      - 422 on invalid `status` (e.g. `"FOO"`)
      - 200 + new row on first sync
      - 200 + same row updated (no duplicate) on second sync with same
        `shopify_subscription_id`
      - `cancelled_at` set when status transitions to CANCELLED;
        preserves earliest timestamp on subsequent CANCELLED retries
      - GET returns null when no record; returns the active record
        when one exists; status filter excludes CANCELLED/EXPIRED

## Phase 5: Polish

- [ ] T011 Run `mypy --strict src/` — clean.
- [ ] T012 Run `ruff check src/ tests/` — clean.
- [ ] T013 Run `pytest tests/api/test_shopify_billing.py -v` — green.
- [ ] T014 Smoke from Shopify-app dev: subscribe to Growth → confirm
      row appears via `psql` or the GET endpoint.
