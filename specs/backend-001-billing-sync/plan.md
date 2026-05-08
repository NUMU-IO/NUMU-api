# Implementation Plan: backend-001 — Billing Sync

**Branch**: `backend-001-billing-sync` (or wherever the user is working)
**Date**: 2026-05-08
**Spec**: [spec.md](./spec.md)

## Summary

Add the missing `POST /api/v1/shopify/{store_id}/billing/sync` endpoint
+ companion GET so the Shopify-app's `syncSubscriptionToNumu` succeeds
and Numu has a persistent record of every merchant's plan. New table
`shopify_subscriptions`, new entity, new repo, new use case, new route
file mounted under the existing `shopify` router.

## Technical Context

- Language: Python 3.11, async/await throughout (Constitution IV).
- ORM: SQLAlchemy 2.0 async; `Mapped`/`mapped_column` style matching
  existing `shopify_app_settings.py` model.
- Migration: Alembic, `alembic/versions/20260508_add_shopify_subscriptions.py`.
- Validation: Pydantic v2 schemas in `src/api/v1/schemas/shopify.py`.
- Auth: existing `verify_internal_key` dependency.
- Tests: pytest async, pattern from existing
  `tests/api/test_*.py` integration tests.

## Constitution Check

| Principle / Constraint | Applies? | Compliance |
|---|---|---|
| I — Privacy by Hashing | N/A | No PII in this table. |
| II — GDPR Recital 47 Fidelity | ✅ | Adds `cancelled_at` for audit; on `shop/redact` the existing webhook handler clears the row alongside other tenant data. |
| III — Spec-First, Tests From Spec | ✅ | New `tests/api/test_shopify_billing.py`. |
| IV — Async-First, Strictly Typed | ✅ | All async; MyPy strict; Pydantic v2 at API boundary. |
| V — Tenant Isolation by RLS | ✅ | Migration enables RLS + policy on `tenant_id`. |
| Alembic discipline | ✅ | Forward-only `up()` + `down()`; RLS in same migration as table. |
| Contract-versioned API responses | ✅ | New endpoint, no breaking change to existing routes. |
| Secret hygiene | ✅ | No new secrets. |

No violations.

## File-by-file changes

### Created

- `src/core/entities/shopify.py` — append `ShopifySubscription` entity
  (status, plan_id, is_trial, trial_ends_at, current_period_end,
  cancelled_at, etc.).
- `src/infrastructure/database/models/tenant/shopify_subscription.py`
  — `ShopifySubscriptionModel` SQLAlchemy mapped class.
- `alembic/versions/20260508_add_shopify_subscriptions.py` —
  table + indexes + RLS policy.
- `src/api/v1/schemas/shopify.py` — append `BillingSyncRequest`,
  `BillingSubscriptionResponse`, `PlanIdLiteral`,
  `SubscriptionStatusLiteral`.
- `src/application/use_cases/shopify/billing_sync.py` — `BillingSyncUseCase`
  that upserts via the repo and returns the entity.
- `src/api/v1/routes/shopify/billing.py` — new router with two endpoints:
  `POST /{store_id}/billing/sync` and `GET /{store_id}/billing/subscription`.
- `tests/api/test_shopify_billing.py` — happy path + 403 + 422 + upsert + cancelled transition.

### Modified

- `src/infrastructure/repositories/shopify_repository.py` — append
  `ShopifySubscriptionRepository` class following the existing pattern.
- `src/api/dependencies/shopify.py` — add `get_shopify_subscription_repo`
  factory.
- `src/api/v1/routes/shopify/__init__.py` — mount the new
  `billing_router`.

## Phase 0 — Research notes

- The Shopify-app side already lives with a best-effort POST that
  catches and logs failures (per the syncSubscriptionToNumu helper).
  No retry on the client side, so we don't need server-side dedup
  beyond the upsert.
- Plan-id validation: Pydantic `Literal["starter", "growth", "scale"]`
  matches the Shopify-app's exported `PlanId` type. Adding a 4th
  plan in the future would require coordination on both repos
  (constitution: contract-versioned API responses).
- Unique index strategy: composite `UNIQUE (store_id,
  shopify_subscription_id)`. Allows multiple historic subscriptions
  per store but only one row per Shopify subscription.

## Phase 1 — Design notes

### `ShopifySubscriptionModel`

```python
class ShopifySubscriptionModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "shopify_subscriptions"
    __table_args__ = (
        UniqueConstraint("store_id", "shopify_subscription_id"),
        {"schema": "public"},
    )
    tenant_id: Mapped[UUID | None]
    store_id: Mapped[UUID]
    shopify_subscription_id: Mapped[str]
    status: Mapped[str]
    plan_id: Mapped[str]
    is_trial: Mapped[bool]
    trial_ends_at: Mapped[datetime | None]
    current_period_end: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]
    synced_at: Mapped[datetime]
```

### Upsert semantics

```python
async def upsert(self, store_id, body) -> ShopifySubscription:
    stmt = (
        insert(ShopifySubscriptionModel)
        .values(...)
        .on_conflict_do_update(
            index_elements=["store_id", "shopify_subscription_id"],
            set_={...},
        )
        .returning(ShopifySubscriptionModel)
    )
    row = (await self.session.execute(stmt)).scalar_one()
    return _to_entity(row)
```

The `cancelled_at` field is set in the update branch only when status
transitions to CANCELLED/EXPIRED/FROZEN (idempotent — uses GREATEST
to preserve the earliest cancellation timestamp on retries).

## Verification

- Local: `pytest tests/api/test_shopify_billing.py -v` — green.
- Manual: from numu-payments-intelligence in dev mode, subscribe to
  Growth → check `shopify_subscriptions` table has the row with
  `plan_id="growth"`, `is_trial=true`, `trial_ends_at` ~14 days out.
- Cancel → row updates to `status="CANCELLED"`, `cancelled_at` set.
