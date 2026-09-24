# NUMU Entitlements and Feature Flags: Design

Status: V1 built 2026-09-24 (NUMU-api `feat/entitlements-v1`, numo-merchant-hub `feat/entitlements-v1`, numu-admin `feat/entitlements-admin`). Scope: NUMU-api, numo-merchant-hub, numu-admin, numu-storefront.

Where V1 differs from this design:

- Phases 0 and 1 of §14 shipped together, without the shadow week. `tests/unit/test_entitlements_seed.py` pins the seed to `PLAN_LIMITS` instead, so the only intended behaviour change is D1.
- D1 is decided; D2, D3 and D6 are applied, and D4 is being applied one field at a time (§22). D5 is still open.
- The hub opens the upgrade dialog from `showError` as well as from the mutation cache, so a caller with its own `onError` still gets it.

What was run while designing (Appendix A has the details):

- The resolver rules: 13 unit tests pass.
- The schema and the quota SQL on Postgres 15: 60 concurrent consumers against a limit of 50 admit exactly 50, and a rolled-back consume leaves the counter untouched.
- The service end to end on the real NUMU models, Postgres and Redis: 8 scenarios.
- The migration seed: an exact match with the live `PLAN_LIMITS` (90 rows, 9 plans).

The dependencies, routes, admin and hub snippets are written against the real code but were not executed.

| Your item | Section |
|---|---|
| 1 Architecture | §3 |
| 2 Schema, 4 Feature model, 5 Flag model, 6 Usage model | §4 |
| 3 SQLAlchemy models | §4.2 |
| 7 Precedence, 8 Resolver | §5 |
| 9 Redis, 10 Invalidation, 17 Expiring overrides | §6 |
| Limits: hard, soft, overage, upgrade | §7 |
| 11 FastAPI design, 12 Backend examples | §8 |
| 18 Percentage rollout | §9 |
| 19 Add-ons, 20 Subscription changes, 21 PAYG | §10 |
| 13 React, 14 Dashboard API contract | §11 |
| Storefront | §12 |
| 15 Admin UI, 16 Audit | §13 |
| 22 Migration | §14 |
| 23 Testing | §15 |
| 24 Races | §16 |
| 25 Security | §17 |
| 26 Performance | §18 |
| 27 Folder structure | §19 |
| 28 Production code | §5, §4, §20 |
| V1, V2, V3 | §21 |
| Mistakes in the proposal | §2 |

---

## 0. The short version

- **Build two small evaluators, not one chain.**
  - *Entitlements* answer "may this tenant use X, and how much?"
  - *Release flags* answer "is release Y switched on for this tenant yet?"
  - A route that needs both asks both. The global kill switch is an AND gate on the entitlement answer. It is not a layer in the precedence order.
- **Reuse what NUMU already has:**

  | Need | Already there |
  |---|---|
  | Subscriptions | `tenants.plan` and the lifecycle columns |
  | Add-ons | NUMU Apps: `app_installations` plus `app_subscriptions`, charged from the wallet |
  | Audit | `audit_logs` and `AuditService` |
  | Cache | `RedisCacheService` |
  | Versioned cache keys | `perms:{tenant}:{user}:v{permission_version}` |

  That leaves 6 new tables and 1 new column. There is no `plans`, `subscriptions` or `subscription_addons` table.
- **Two value kinds: boolean and limit.** A limit is a non-negative integer or the string `"unlimited"`. There is no `-1`, no float, and no JSON config blob.
- **Precedence, in order:**
  1. A live override. It is absolute and can grant or deny.
  2. The plan merged with any live add-ons: booleans are OR'd, limits add up, and `unlimited` absorbs everything else.
  3. The feature default.

  The kill switch is then AND'd with the result. Every answer carries `source`, `source_id`, `expires_at`, and the layers it overrode, which is what "why" shows.
- **Cache.** One Redis key per tenant, stamped with `[tenants.entitlements_version, catalog token, tenants.plan]`. A stamp that doesn't match is recomputed on the spot.
  - Plan changes need no invalidation code, because the plan is part of the stamp.
  - Overrides and add-ons bump the version in the same transaction as the change.
  - Catalog and flag edits rotate the token after they commit.
  - The TTL is the earlier of 5 minutes and the next moment an override or add-on starts or ends, so expiry needs no cron job.
- **Usage.**
  - Resource limits (products, staff, stores) are COUNTs of the real rows, taken under an advisory lock.
  - Consumable meters (messages, automation runs) are `usage_counters` rows. They are updated by one atomic conditional upsert in the caller's transaction.
  - Never block a shopper. Limits on anything a customer triggers are soft.
- **Cost.** A cache hit is 1 Redis round trip and 0 database queries. A miss is about 6 small queries.
- **The bigger risks are in today's code, not the new code:**
  - enforcement that can be bypassed
  - plan fields that are never enforced
  - a hub that throws away 403 error codes

  §1 lists them and §14 fixes them in a safe order.

---

## 1. What NUMU has today

### 1.1 Facts that shape the design

1. **NUMU is not schema-per-tenant.**
   - All 89 "tenant" models declare `{"schema": "public"}`, with a `tenant_id`/`store_id` discriminator, and are isolated by Postgres RLS (84 migrations add policies).
   - There is no `CREATE SCHEMA` anywhere. `TenantMiddleware`'s `SET search_path TO tenant_<subdomain>, public` names schemas that don't exist, so it does nothing.
   - This helps: the new platform tables sit next to the tenant tables, and usage counts are ordinary indexed queries.
2. **One tenant is one store is one billing subject.**
   - `CreateStoreUseCase` creates a new tenant for every store, so one owner can hold several tenants.
   - Everything in this design is keyed by `tenant_id`. What you called `merchant_id` is the tenant.
   - The only owner-level limit, stores, keeps today's rule: use the plan of the owner's primary tenant (`stores.py:233-270`).
3. **There are nineteen separate gating mechanisms:**
   - `PLAN_LIMITS`
   - `PlanLimitService` and its dependencies
   - about 30 hardcoded plan-name conditionals
   - `_PLAN_RANK` and `_UPGRADE_MAP`
   - the `tenants.feature_flags` JSON column
   - the `settings.ff_*` flags
   - 11 `platform_config` switches
   - `CapabilityService`
   - `whatsapp_entitlement`
   - NUMU Apps
   - paid app subscriptions
   - `api_access`
   - the wallet, go-live and billing-lock gates
   - the partner-program switches
   - theme `required_plan`
   - marketplace theme purchases
   - the Shopify-app tiers
   - the agent quota
   - the lifecycle guards

| Mechanism | Verdict |
|---|---|
| `PLAN_LIMITS` feature fields (`max_*`, `*_enabled`) | Replace with `plan_entitlements`. Keep only `display_name`, the prices and `commission_bps` |
| `PlanLimitService`, `api/dependencies/plan.py` | Replace with `EntitlementService`, then delete |
| `CapabilityService` `_PLAN_RANK` / `min_plan` | Replace the plan floor with `has()`. Keep the per-store sector overrides: they are merchant configuration, not entitlements |
| `api_access.py` (plan OR grant) | Keep the shape. It becomes `has(tenant, "api_access")`, and the grant becomes an override row |
| `tenants.feature_flags` | Stop writing to it. Rollout keys move to `feature_flag_targets` and `api_access` becomes an override. `golive_exempt` and `wallet_checkout_gate` are billing exemptions; leave them for now |
| `settings.ff_*` | Move each one to `feature_flags` when you next touch it (flipping one then needs no deploy). Delete `ff_apply_offers_at_checkout`, which nothing reads |
| `platform_config` switches | Keep. They are platform configuration, not per-tenant state |
| NUMU Apps and `app_subscriptions` | Keep. This is the add-on layer |
| `whatsapp_entitlement` | Keep in V1: it owns the allowance and the state machine. Fold it into the resolver in V2 |
| Platform capability registry, theme purchases, Shopify-app tiers | Unrelated systems; leave them alone |
| `require_feature_flag`, `require_capability`, `require_discount_feature`, `check_store_limit`, `require_custom_domain`, `require_writable_tenant`, `demo_guard` | Dead: none is applied anywhere. Delete |

### 1.2 Bugs and drift found during the audit

These are the case for doing this at all.

| # | Finding | Where |
|---|---|---|
| B1 | The monthly order cap applies only to orders created in the hub. Storefront checkout, CSV import and TikTok ingestion skip it, so the trial's 500 orders a month is never enforced where it matters. | `api/dependencies/plan.py`, mounted on 3 routes |
| B2 | The product cap is skipped by CSV import, the agent's `create_product` tool and social import. | `products.py:1302` and others |
| B3 | Starter can create webhooks (`webhooks_enabled=True`), but they are never delivered, because delivery requires API access, which is Pro only. | `webhook_delivery_service.py:53-65` |
| B4 | Unknown plans fall back to different places: trial in `get_plan_features`, and "free" in `PlanLimitService`, `api_access` and `CapabilityService`. `beta`, which `public/beta.py:126` writes, is in no vocabulary. It gets trial limits and capability rank 0, and it escapes the go-live gate. | several |
| B5 | Admin plan-limit edits are hot-patched into module globals. API workers re-read them every 5 minutes; **Celery never does**. So `subscription_renewal_task.py:124` renews at the code-default prices whenever an admin has changed a price. Check whether prod's `platform_config.plan_limits` row changes any price. | `main.py:128-182` |
| B6 | The admin PATCH writes `tenants.plan` without validating it (`tenants.py:339-340`). SQLAdmin's `TenantAdmin` can edit `plan` and `feature_flags` without writing an audit row. | |
| B7 | `max_staff_members`, `max_customers`, `analytics_enabled`, `custom_domain_enabled` and `discount_codes_enabled` are defined and shown in the admin, but **never enforced**. Turning enforcement on would take something away from live merchants. | |
| B8 | The hub throws away every 403 body. `api.ts:235-264` reads `body.detail`, but the API sends `body.error`. Any new `FEATURE_NOT_AVAILABLE` code would therefore be invisible. The 402 `PLAN_LIMIT_EXCEEDED` also has no handling, and several components read the wrong field. | hub |
| B9 | The hub does not refetch `/auth/me` when the merchant switches store, so the plan and flags go stale for merchants with several stores. | hub `StoreContext.tsx:169-178` |
| B10 | WhatsApp's `count_messages` filters `message_logs` by `store_id` alone, and there is no `(store_id, created_at)` index. It runs on every send and on checkout's OTP check, and it gets slower as the store's message history grows. | `whatsapp_entitlement.py:99-117` |
| B11 | Your prompt says Starter has unlimited products and 100 orders a month. The code says Starter has 100 products and unlimited orders, and the trial has 500 orders. | `plan.py:100-114` |
| B12 | The public store payload includes the whole `tenants.feature_flags` map: grants, billing exemptions and rollout keys. Anyone can read it. | `storefront/public.py:994` |
| B13 | No migration revokes Supabase's `anon`/`authenticated` grants, and the wallet tables have no RLS. If the Supabase Data API is on, those tables may be reachable with the public anon key. **I could not check this from here. Please check it.** | Supabase project |

---

## 2. Review of the proposed design

**Keep these parts:**
- entitlements kept separate from flags
- merchant overrides with an expiry and a reason
- the "explain" output
- central platform tables
- a Redis cache with explicit invalidation
- enforcement on the backend

**Change these:**

1. **Flags are inside the entitlement chain.** "Global disable → environment flag → override → …" puts release concerns into a precedence list, which is the mixing you wanted to avoid.
   - A kill switch or an off flag is not a higher-priority value. It is an AND.
   - Use two evaluators and combine them at the call site: `require_flag("multi_warehouse_v1")` returns 404 when the flag is off, and `require_feature("multi_warehouse")` returns 403 when the tenant is not entitled.
2. **"Temporary promotion" is its own layer.**
   - A promotion for one merchant is an override with `source='promotion'` and an `expires_at`.
   - A promotion for a group ("every Starter merchant gets X during Ramadan") is a time-boxed plan grant, or a batch of overrides that share one reason.
   - A seventh layer adds a precedence question nobody can answer from memory.
3. **Add-ons and plans are in a precedence order.** First match wins is wrong for purchases. A "+2 staff" pack on Pro's 10 must give 12, not 2. Plans and add-ons merge: booleans are OR'd, limits add up, and `unlimited` absorbs the rest.
4. **Environment is modelled as data.**
   - Each NUMU environment already has its own database: prod on Supabase, test and staging on the droplet.
   - The admin already has an environment switcher (`client/src/lib/env.ts`: prod, stage, test).
   - A row in the prod database *is* the prod setting. An `environment` column would either be redundant or need a cross-environment control plane NUMU doesn't have.
5. **The CONFIG type.** `automation: {enabled, max_workflows, max_monthly_runs}` makes every operation harder:
   - "Give merchant X 20 workflows" would have to override the whole blob.
   - There is no obvious rule for merging two blobs.
   - The UI would need a schema for each blob.

   Use flat keys instead (`automation`, `automation_workflows`, `automation_runs_per_month`) and group them with `category` for display.
6. **DECIMAL_LIMIT.** Floats drift in quota arithmetic. Use integers in the smallest unit (piasters, MB) and a `unit` column.
7. **`subscriptions` and `subscription_addons` tables.** NUMU already has both under other names:
   - `tenants.plan` and the lifecycle columns
   - `app_installations` and `app_subscriptions`, charged from the wallet with a 3-day grace period

   A second copy would disagree with the first. That is the same reason `capability_service.py`'s docstring gives for not building a `store_capabilities` table.
8. **A `plans` table.** Not in V1. Plan identity and price live in `tenants.plan` and `PLAN_LIMITS`, and `plan_entitlements` is keyed by the same string. Do validate plan writes against the `PLAN_LIMITS` keys (B4, B6).
9. **`usage_counters` for everything.**
   - Resource limits (staff, products, locations) must be COUNTs of the real rows. A counter drifts every time some delete, import or restore path forgets to update it.
   - Counters are for consumption, such as messages and runs, where there is no row you can count cheaply.
10. **`entitlements:{merchant_id}` with a DELETE on every change.** This has a classic race. A request that read the old data before your commit writes it back after your DELETE, and the stale value survives until the TTL. Versioned stamps rule that out (§6).
11. **Resolving in middleware.** Don't compute entitlements on every request. Resolve them only when a route or use case asks. Most requests, storefront pages for example, never need them.

---

## 3. Recommended architecture

```
                 ┌────────────── written by migrations / admin ───────────────┐
                 │  features ── plan_entitlements ── entitlement_overrides    │
                 │  (catalog,    (plan_key = plan     (per-tenant, absolute,  │
                 │   kill switch) or addon:<slug>)     time-boxed, audited)   │
                 │                                                            │
                 │  feature_flags ── feature_flag_targets     usage_counters  │
                 └───────────────┬───────────────────────────────┬────────────┘
   written by billing            │                               │
   ─────────────────             ▼                               ▼
   tenants.plan  ─────►  src/core/entitlements.py      EntitlementService
   app_subscriptions ──►  resolve()  flag_on()   ◄──── snapshot / require /
   (wallet charges)       (pure, no I/O)               limit / flag / check_quota /
                                                       consume / usage / explain
                                                            │ one Redis MGET
                                                            ▼
                                     ent:{tenant_id}  stamped [version, token, plan]
                                                            │
          ┌──────────────────────┬──────────────────────────┼─────────────────────┐
          ▼                      ▼                          ▼                     ▼
   FastAPI deps            use cases / Celery        GET /stores/{id}/      admin explain
   require_feature()       check_quota(), consume()  entitlements (hub)     (never cached)
   require_flag()
```

| Concept | Question it answers | Lives in | Written by |
|---|---|---|---|
| Feature catalog | What exists? Its kind, meter and kill switch | `features` | migrations create rows; the admin edits names, enforcement and the kill switch |
| Entitlement grants | What does plan or add-on P include? | `plan_entitlements` | admin |
| Overrides | What exception does tenant T have? | `entitlement_overrides` | admin |
| Subscription | Which plan is T on? | `tenants.plan` (+ lifecycle) | billing use cases |
| Add-ons | Which paid apps cover T right now? | `app_subscriptions` | `app_billing` |
| Release flags | Is release R on for T? | `feature_flags`, `feature_flag_targets` | admin |
| Usage | How much has T used? | the real rows (COUNT), or `usage_counters` | the write path of the feature itself |
| Billing | What does T pay? | the wallet ledger, InstaPay and Kashier intents, prices in `PLAN_LIMITS` | billing only |

**Rules:**
- **Feature code** calls `require`, `has`, `limit`, `check_quota` or `consume`. It never reads `tenants.plan`.
- **Billing code may read `tenants.plan`.** Examples: payg activation, `INSTAPAY_PAYABLE_PLANS`, the developer-sandbox rules, MRR reports. To decide which side a check belongs on:
  - Could it be sold, given away by support, or switched off during an incident? It is an entitlement.
  - Is it what the plan *is*? It is plan identity, and billing owns it.
- **Entitlements and billing stay apart.** Entitlements never read the wallet, and billing never writes the entitlement tables. There are exactly two points of contact:
  - billing bumps the tenant's version whenever it changes add-ons
  - app billing must not charge for an add-on the merchant's plan already includes
- **Account state is not an entitlement.** Trial expiry, read-only mode and the billing lock stay where they are: `is_read_only`, `storefront_lock` and the checkout gates.

---

## 4. Data model

### 4.1 PostgreSQL schema

These are platform tables in `public`, following the pattern of the wallet and billing tables. They carry explicit tenant keys and have no tenant RLS policy, because only the API writes them. The DDL below is the version that was run on Postgres 15. `IF NOT EXISTS` was added afterwards, following NUMU's idempotent-migration rule.

```sql
-- A value is true/false (boolean features) or a non-negative integer or the
-- string "unlimited" (limit features). Never -1. Kind/value agreement is
-- checked in the service (cross-table); the shape is checked here.

CREATE TABLE IF NOT EXISTS public.features (
    key             varchar(64)  PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    name            varchar(120) NOT NULL,
    name_ar         varchar(120) NOT NULL,
    description     text,
    category        varchar(40),
    kind            varchar(10)  NOT NULL CHECK (kind IN ('boolean', 'limit')),
    default_value   jsonb        NOT NULL,
    usage           varchar(10)  CHECK (usage IN ('count', 'counter')),
    period          varchar(10)  CHECK (period IN ('day', 'month')),
    enforcement     varchar(10)  NOT NULL DEFAULT 'hard'
                                 CHECK (enforcement IN ('hard', 'soft')),
    unit            varchar(20),
    is_enabled      boolean      NOT NULL DEFAULT true,   -- global kill switch
    disabled_reason text,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_features_default_value CHECK (
        CASE jsonb_typeof(default_value)
            WHEN 'boolean' THEN true
            WHEN 'number'  THEN default_value::numeric >= 0
                            AND default_value::numeric = trunc(default_value::numeric)
            WHEN 'string'  THEN default_value = '"unlimited"'::jsonb
            ELSE false
        END),
    CONSTRAINT ck_features_usage_only_on_limits
        CHECK (kind = 'limit' OR (usage IS NULL AND period IS NULL)),
    CONSTRAINT ck_features_period_needs_usage
        CHECK (period IS NULL OR usage IS NOT NULL)
);

-- What each plan, and each add-on bundle ('addon:<app slug>'), grants.
CREATE TABLE IF NOT EXISTS public.plan_entitlements (
    plan_key    varchar(64) NOT NULL,
    feature_key varchar(64) NOT NULL REFERENCES public.features (key) ON DELETE CASCADE,
    value       jsonb       NOT NULL,
    updated_by  uuid        REFERENCES public.users (id) ON DELETE SET NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (plan_key, feature_key),
    CONSTRAINT ck_plan_entitlements_value CHECK (
        CASE jsonb_typeof(value)
            WHEN 'boolean' THEN true
            WHEN 'number'  THEN value::numeric >= 0 AND value::numeric = trunc(value::numeric)
            WHEN 'string'  THEN value = '"unlimited"'::jsonb
            ELSE false
        END)
);

-- Per-tenant exceptions: support, sales deals, promos, beta, contracts, tests.
CREATE TABLE IF NOT EXISTS public.entitlement_overrides (
    id          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid         NOT NULL REFERENCES public.tenants (id) ON DELETE CASCADE,
    feature_key varchar(64)  NOT NULL REFERENCES public.features (key) ON DELETE CASCADE,
    value       jsonb        NOT NULL,
    starts_at   timestamptz  NOT NULL DEFAULT now(),
    expires_at  timestamptz,
    source      varchar(20)  NOT NULL CHECK (source IN
                ('support', 'sales', 'promotion', 'beta', 'contract', 'testing', 'migration')),
    reason      text         NOT NULL CHECK (length(btrim(reason)) >= 3),
    created_by  uuid         REFERENCES public.users (id) ON DELETE SET NULL,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    revoked_at  timestamptz,
    revoked_by  uuid         REFERENCES public.users (id) ON DELETE SET NULL,
    CONSTRAINT ck_entitlement_overrides_window
        CHECK (expires_at IS NULL OR expires_at > starts_at),
    CONSTRAINT ck_entitlement_overrides_value CHECK (
        CASE jsonb_typeof(value)
            WHEN 'boolean' THEN true
            WHEN 'number'  THEN value::numeric >= 0 AND value::numeric = trunc(value::numeric)
            WHEN 'string'  THEN value = '"unlimited"'::jsonb
            ELSE false
        END)
);
-- At most one live override per (tenant, feature): a new one revokes the old
-- one in the same transaction. Also serves the per-tenant lookup.
CREATE UNIQUE INDEX IF NOT EXISTS uq_entitlement_overrides_live
    ON public.entitlement_overrides (tenant_id, feature_key)
    WHERE revoked_at IS NULL;

-- Release flags. No plan logic in here, ever.
CREATE TABLE IF NOT EXISTS public.feature_flags (
    key             varchar(64) PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    description     text        NOT NULL,
    owner           varchar(120),
    feature_key     varchar(64) REFERENCES public.features (key) ON DELETE SET NULL,
    enabled         boolean     NOT NULL DEFAULT false,
    rollout_percent smallint    NOT NULL DEFAULT 0 CHECK (rollout_percent BETWEEN 0 AND 100),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.feature_flag_targets (
    flag_key   varchar(64) NOT NULL REFERENCES public.feature_flags (key) ON DELETE CASCADE,
    tenant_id  uuid        NOT NULL REFERENCES public.tenants (id) ON DELETE CASCADE,
    enabled    boolean     NOT NULL DEFAULT true,
    expires_at timestamptz,
    reason     text,
    created_by uuid        REFERENCES public.users (id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (flag_key, tenant_id)
);
CREATE INDEX IF NOT EXISTS ix_feature_flag_targets_tenant
    ON public.feature_flag_targets (tenant_id);

-- Consumable meters only. Resource counts are live COUNTs of the real rows.
CREATE TABLE IF NOT EXISTS public.usage_counters (
    tenant_id    uuid        NOT NULL REFERENCES public.tenants (id) ON DELETE CASCADE,
    feature_key  varchar(64) NOT NULL REFERENCES public.features (key) ON DELETE CASCADE,
    period_start timestamptz NOT NULL,     -- day/month bucket start; 1970-01-01 = lifetime
    used         bigint      NOT NULL DEFAULT 0 CHECK (used >= 0),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, feature_key, period_start)
);

-- Cache stamp. Bumped in the same transaction as any per-tenant grant change.
ALTER TABLE public.tenants
    ADD COLUMN IF NOT EXISTS entitlements_version integer NOT NULL DEFAULT 1;

-- Supabase serves `public` over its Data API. These rows decide what a
-- merchant may use, so no role but the API's own may touch them.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON public.features, public.plan_entitlements,
            public.entitlement_overrides, public.feature_flags,
            public.feature_flag_targets, public.usage_counters
            FROM anon, authenticated;
    END IF;
END $$;
```

**A second, separate migration adds an index for the History tabs.** They read `audit_logs` by resource, but today only `event_type`, tenant, user, store and `created_at` are indexed. `audit_logs` takes a write on every login, so build the index `CONCURRENTLY`, following `20260501_analytics_indexes.py` (`op.execute("COMMIT")` first):

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_logs_resource
    ON public.audit_logs (resource_type, resource_id, created_at);
```

**Why these tables and no others**
- **`features`** is the catalog. Migrations create its rows, never the admin UI: a feature with no code behind it is a switch that does nothing. That lesson is already written into `capability_service.py`'s `implemented` flag.
- **`plan_entitlements`** holds plans and add-ons together. `plan_key` is either a plan (`starter`) or an add-on bundle (`addon:cod_shield`). This is Stripe's model (products grant features, and a subscription can hold several products) without a new table, and the admin edits both in one matrix.
- **`entitlement_overrides`** allows one live row per (tenant, feature). Creating a new override revokes the old one, so there is only ever one override to explain. History comes from the revoked rows plus `audit_logs`.
- **`feature_flags.feature_key`** is for display only. It lets the feature page list its releases. The resolver never reads it.
- **`tenants.entitlements_version`** is already loaded on every hub request, because `TenantMiddleware` fetches the tenant row. That makes the version check free.

### 4.2 SQLAlchemy models

`src/infrastructure/database/models/public/entitlements.py`. Add the six models to the models package `__all__`: that is how alembic sees them, and how SQLAdmin builds its views (make those views read-only, §17). Add `entitlements_version` to `TenantModel`:

```python
    # Cache stamp for EntitlementService. Bumped by EntitlementService.bump_tenant
    # in the same transaction as any override, add-on or flag-target change.
    entitlements_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", default=1
    )
```

```python
"""Entitlements, release flags and usage counters (public schema).

Platform tables, like the wallet and billing ones: they decide what a merchant
may use, so only the API writes them and they carry explicit tenant keys.
Value shape (true/false, a non-negative int, or "unlimited") is enforced by
CHECK constraints in the migration; agreement with the feature's kind is
enforced by ``check_value`` in the service.
"""

from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class FeatureModel(Base, TimestampMixin):
    """The catalog. Rows are created by migrations, never by the admin UI:
    a feature without code behind it is a switch that does nothing."""

    __tablename__ = "features"
    __table_args__ = {"schema": "public"}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_ar: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(40))
    #: boolean | limit
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    default_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    #: limits only. count = COUNT the real rows; counter = usage_counters.
    usage: Mapped[str | None] = mapped_column(String(10))
    #: day | month | None (lifetime / current total)
    period: Mapped[str | None] = mapped_column(String(10))
    #: hard = refuse past the limit; soft = allow, record, notify.
    enforcement: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="hard"
    )
    unit: Mapped[str | None] = mapped_column(String(20))
    #: The global kill switch.
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    disabled_reason: Mapped[str | None] = mapped_column(Text)


class PlanEntitlementModel(Base):
    """What a plan grants. Add-on bundles use ``plan_key = 'addon:<app slug>'``."""

    __tablename__ = "plan_entitlements"
    __table_args__ = {"schema": "public"}

    plan_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    feature_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("public.features.key", ondelete="CASCADE"),
        primary_key=True,
    )
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EntitlementOverrideModel(Base, UUIDMixin):
    """A per-tenant exception. Absolute: it replaces the plan value, it does
    not add to it. At most one live row per (tenant, feature)."""

    __tablename__ = "entitlement_overrides"
    __table_args__ = (
        Index(
            "uq_entitlement_overrides_live",
            "tenant_id",
            "feature_key",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("public.features.key", ondelete="CASCADE"),
        nullable=False,
    )
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: support | sales | promotion | beta | contract | testing | migration
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="SET NULL")
    )


class FeatureFlagModel(Base, TimestampMixin):
    """A release switch. Knows nothing about plans."""

    __tablename__ = "feature_flags"
    __table_args__ = {"schema": "public"}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(120))
    #: Display grouping only (admin "Releases" tab); never read by the resolver.
    feature_key: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("public.features.key", ondelete="SET NULL")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    rollout_percent: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )


class FeatureFlagTargetModel(Base):
    __tablename__ = "feature_flag_targets"
    __table_args__ = (
        Index("ix_feature_flag_targets_tenant", "tenant_id"),
        {"schema": "public"},
    )

    flag_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("public.feature_flags.key", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UsageCounterModel(Base):
    """Consumable meters. One row per tenant, feature and period bucket."""

    __tablename__ = "usage_counters"
    __table_args__ = {"schema": "public"}

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    feature_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("public.features.key", ondelete="CASCADE"),
        primary_key=True,
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    used: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

### 4.3 Features and values

| kind | value | `has()` is true when | example |
|---|---|---|---|
| `boolean` | `true` / `false` | the value is `true` | `advanced_analytics`, `custom_domain` |
| `limit` | an integer ≥ 0, or `"unlimited"` | the value is `"unlimited"` or greater than 0 | `products`, `staff_accounts`, `orders_per_month` |

- **Unlimited.** In the database, cache and API it is the string `"unlimited"`. In Python it is `UNLIMITED = "unlimited"`.
  - It reads as what it means: SQL, logs and admin screens all show `"unlimited"`.
  - It can't be mistaken for a number. With `-1`, `used >= -1` quietly refuses every unlimited tenant.
  - Every comparison has to handle it explicitly, and `check_value` rejects `-1`.
- **Booleans are checked with `type(v) is bool`, never `isinstance`.** In Python `True` is an `int`, so `isinstance` would accept `True` as a limit of 1. There is a test for this.
- **Grouping.** A capability that was a CONFIG blob becomes several flat keys that share a `category`. For automation, that is `automation` (boolean), `automation_workflows` (limit, count) and `automation_runs_per_month` (limit, counter, month).
- **Defaults.** A default is what a tenant gets when its plan has no row. That only happens by mistake, and it raises an `entitlements_unknown_plan` alert. Choose the value that fails safe for the merchant's *customers*. `orders_per_month` defaults to `"unlimited"`, because a missing row must never block checkout. Paid booleans default to `false`.

**The V1 catalog is exactly what `PLAN_LIMITS` and `CapabilityService` gate today, and nothing new:**

| key | kind | usage | period | enforcement | from |
|---|---|---|---|---|---|
| `products` | limit | count | – | hard | `max_products` |
| `orders_per_month` | limit | count | month | **soft** (decision D2) | `max_orders_per_month` |
| `stores` | limit | count | – | hard | `max_stores` (owner level) |
| `staff_accounts` | limit | count | – | hard (not enforced today, D4) | `max_staff_members` |
| `partner_apps` | limit | count | – | hard | `max_partner_apps` |
| `api_access` | boolean | | | | `api_access_enabled`, plus the `feature_flags.api_access` grants |
| `custom_domain` | boolean | | | | `custom_domain_enabled` (not enforced today) |
| `discount_codes` | boolean | | | | `discount_codes_enabled` (not enforced today) |
| `multi_warehouse` | boolean | | | | capability `min_plan="pro"` |
| `product_subscriptions` | boolean | | | | capability `min_plan="pro"` |

- **Dropped:**
  - `max_customers`: enforcing it would block shoppers at checkout.
  - `analytics_enabled`: never enforced, and basic analytics is for everyone.
  - `webhooks_enabled`: fold it into `api_access`, which is what delivery already checks (decision D3).
- **Added when each feature ships:** `advanced_analytics`, `automation*`, `abandoned_cart`, `cod_shield`, `reviews`, `bundles`, `whatsapp*`. Each one gets its catalog row, its grants and a grandfather query in the same migration (§14).

### 4.4 Flags

| field | meaning |
|---|---|
| `enabled` | The master switch. `false` turns the flag off for everyone, targets included. This is the flag's kill switch |
| `rollout_percent` | 0–100. Applies to tenants that have no target |
| targets | Per tenant, on or off, with an optional `expires_at` and a reason |

**How to express each rollout state:**

| State | Settings |
|---|---|
| Everyone | `enabled=true, rollout_percent=100` |
| Selected merchants | `enabled=true, 0%`, plus targets |
| A percentage | `enabled=true, N%` |
| Hold one tenant back from a full rollout | a target with `enabled=false` |
| Environment | the database you're in (§2.4) |
| Expiry | `expires_at` on the target |

- **Who creates flags.** An admin creates them in the UI, or a data migration if you want the flag seeded. A flag that doesn't exist evaluates to off, which is always the safe side.
- **Why flags don't use migrations only.** Unlike features, a flag carries no grants, so a migration per flag would only add alembic heads, and NUMU already fights head drift.

### 4.5 Usage

| meter | how "used" is measured | examples | why |
|---|---|---|---|
| `count` | `COUNT(*)` of the real rows, through an indexed foreign key | products, staff, stores, locations; orders this month via `ix_orders_store_created_status` | The rows are the truth, so nothing can drift. The cost is bounded by the limit, because only limited tenants are ever counted |
| `counter` | a `usage_counters` row per (tenant, feature, period) | WhatsApp messages, automation runs, API calls (flushed from Redis, V2), storage bytes (V2) | There is no row to count cheaply, or billing needs an exact per-period number |

Periods are `day`, `month` or none (lifetime or the current total), bucketed by the UTC calendar. For a merchant in Cairo the month resets at 02:00 or 03:00 local time on the 1st. Billing-anchored periods (WhatsApp's "resets on the 9th") come in V2.

---

## 5. Resolution precedence and the resolver

```
 entitlement: may tenant T use feature F, and how much?
 ─────────────────────────────────────────────────────
  1. live override (T, F)            absolute: can grant OR deny
         │ none
  2. plan ⊕ live add-ons             boolean: OR   limit: sum, "unlimited" absorbs
         │ none
  3. feature default
         │
         ▼
  AND  features.is_enabled           the global kill switch; value kept for "why"
         ▼
  { value, available, source, source_id, expires_at, reason, shadowed }

 release flag: is release R on for tenant T?
 ─────────────────────────────────────────────
  1. flag.enabled = false            off for everyone (flag kill switch)
  2. unexpired target (R, T)         on or off
  3. bucket(R, T) < percent × 100    on
  4. off
```

**Why overrides sit above purchases.** An override is a deliberate human decision: support, sales, abuse handling or a contract. A deny has to beat a purchase, for example to block fraud. If a blocked merchant then buys the add-on, an admin has to revoke the override. That is intended: a human decision holds until it is revoked or expires, and the explain view always shows it winning.

**Why an override replaces the value and doesn't add to it.** It keeps the answer predictable. "Plan + 5" is written as the number.

- **Trap:** an override set below the plan value quietly *reduces* access after an upgrade.
- **Mitigation:** the admin UI warns when an override is lower than the grants it shadows, and explain shows the value that was shadowed.

`src/core/entitlements.py` has no I/O and was run by the tests in §15:

```python
"""Entitlements and feature flags: the rules, with no I/O.

Two questions, kept apart on purpose:

* entitlement: may this tenant use feature X, and how much of it?
  (plans, add-ons, admin overrides, the global kill switch)
* flag: is release Y switched on for this tenant yet?
  (master switch, per-tenant targets, percentage rollout)

A route that needs both asks both. Nothing here reads the database, Redis or
the clock, so every rule can be tested as a plain function call.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

#: The only non-numeric limit value. Never -1: a negative number still reads
#: as a limit, so `used < limit` quietly fails for every unlimited tenant.
UNLIMITED = "unlimited"

Value = bool | int | Literal["unlimited"]
Kind = Literal["boolean", "limit"]


@dataclass(frozen=True)
class Feature:
    key: str
    kind: Kind
    default: Value
    #: False is the global kill switch: nobody may use it, whatever they paid.
    enabled: bool = True


@dataclass(frozen=True)
class Grant:
    """One layer that offers a value: a plan, an add-on, an override."""

    #: plan | addon | override | default
    source: str
    source_id: str | None
    value: Value
    starts_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Resolved:
    key: str
    value: Value
    source: str
    source_id: str | None
    #: When this value next changes on its own, if ever.
    expires_at: datetime | None
    available: bool
    #: disabled_globally | blocked | not_in_plan, or None when available.
    reason: str | None
    #: Layers that lost to an override, so "why" can show the whole stack.
    shadowed: tuple[Grant, ...] = ()


def check_value(kind: Kind, value: object) -> Value:
    """The value, if it is valid for the kind. Raises ValueError otherwise.

    ``type(...) is`` on purpose: ``True`` is an ``int`` in Python, so
    ``isinstance`` would accept ``True`` as a limit of 1.
    """
    if kind == "boolean" and type(value) is bool:
        return value
    if kind == "limit" and (value == UNLIMITED or (type(value) is int and value >= 0)):
        return value  # type: ignore[return-value]
    raise ValueError(f"{value!r} is not a valid {kind} value")


def is_on(kind: Kind, value: Value) -> bool:
    if kind == "boolean":
        return value is True
    return value == UNLIMITED or value > 0  # type: ignore[operator]


def active(grant: Grant, now: datetime) -> bool:
    return (grant.starts_at is None or grant.starts_at <= now) and (
        grant.expires_at is None or now < grant.expires_at
    )


def _combine(kind: Kind, a: Value, b: Value) -> Value:
    if kind == "boolean":
        return a is True or b is True
    if UNLIMITED in (a, b):
        return UNLIMITED
    return a + b  # type: ignore[operator]


def resolve(
    feature: Feature,
    *,
    bundles: list[Grant],
    override: Grant | None,
    now: datetime,
) -> Resolved:
    """One feature for one tenant.

    Order: a live override wins outright (it can grant or deny); otherwise the
    plan and every live add-on merge (booleans OR, limits add up, unlimited
    absorbs); otherwise the feature default. The kill switch is not a layer in
    that order: it gates the result and leaves the value visible for "why".
    """
    live = [g for g in bundles if active(g, now)]
    shadowed: tuple[Grant, ...] = ()
    if override is not None and active(override, now):
        value: Value = override.value
        winners = [override]
        shadowed = tuple(live)
    elif live:
        value = live[0].value
        for grant in live[1:]:
            value = _combine(feature.kind, value, grant.value)
        winners = [
            g for g in live if feature.kind == "limit" or g.value is True
        ] or live[:1]
    else:
        value = feature.default
        winners = [Grant("default", None, feature.default)]

    ends = [g.expires_at for g in winners]
    if feature.kind == "boolean" and value is True:
        # Stays on while any grant still gives it.
        expires_at = None if None in ends else max(e for e in ends if e)
    else:
        # A limit changes when any contributor drops out.
        expires_at = min((e for e in ends if e), default=None)

    on = is_on(feature.kind, value)
    if not feature.enabled:
        reason: str | None = "disabled_globally"
    elif not on:
        reason = "blocked" if winners[0].source == "override" else "not_in_plan"
    else:
        reason = None
    return Resolved(
        key=feature.key,
        value=value,
        source=winners[0].source,
        source_id=winners[0].source_id,
        expires_at=expires_at,
        available=feature.enabled and on,
        reason=reason,
        shadowed=shadowed,
    )


def next_change(grants: list[Grant], now: datetime) -> datetime | None:
    """The earliest future moment any grant starts or ends.

    A cached snapshot must not outlive it, which is what makes expiry a
    WHERE clause instead of a cron job.
    """
    return min(
        (t for g in grants for t in (g.starts_at, g.expires_at) if t and t > now),
        default=None,
    )


# ─── Flags ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Flag:
    key: str
    #: Master switch. False turns the release off for everyone, targets too.
    enabled: bool
    #: 0-100, for tenants without a target.
    rollout_percent: int


@dataclass(frozen=True)
class FlagTarget:
    enabled: bool
    expires_at: datetime | None = None


def bucket(flag_key: str, tenant_id: str) -> int:
    """A stable slot in 0-9999 for (flag, tenant).

    sha256, not hash(): Python salts hash() per process, so one tenant would
    land in a different bucket on every worker. The flag key is part of the
    input so each rollout samples a different slice of tenants, instead of
    the same unlucky 10% getting every beta.
    """
    digest = hashlib.sha256(f"{flag_key}:{tenant_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 10_000


def flag_on(
    flag: Flag | None,
    tenant_id: str | None,
    target: FlagTarget | None,
    now: datetime,
) -> tuple[bool, str]:
    """Whether the flag is on for this tenant, and why."""
    if flag is None:
        return False, "unknown_flag"
    if not flag.enabled:
        return False, "flag_off"
    if target is not None and (target.expires_at is None or now < target.expires_at):
        return target.enabled, "targeted"
    if flag.rollout_percent >= 100:
        return True, "everyone"
    if tenant_id and bucket(flag.key, tenant_id) < flag.rollout_percent * 100:
        return True, "rollout"
    return False, "not_in_rollout"
```

The explain output, from the admin endpoint (`EntitlementService.explain`, §20.1):

```json
{
  "value": true, "available": true, "source": "override", "source_id": "8c1e…",
  "expires_at": "2026-10-24T12:00:00+00:00", "reason": null,
  "kind": "boolean", "usage": null, "period": null, "enforcement": "hard",
  "layers": [
    {"layer": "kill_switch", "enabled": true, "reason": null},
    {"layer": "override", "row": {"id": "8c1e…", "value": true, "source": "beta",
      "reason": "early access #88", "created_by": "5b0d…",
      "starts_at": "2026-09-24T12:00:00+00:00", "expires_at": "2026-10-24T12:00:00+00:00",
      "live": true}},
    {"layer": "plan", "id": "starter", "value": false, "expires_at": null,
     "live": true, "shadowed": true},
    {"layer": "addon", "id": "multi_location", "value": true,
     "expires_at": "2026-09-01T00:00:00+00:00", "live": false, "shadowed": false},
    {"layer": "default", "value": false}
  ],
  "cache_agrees": true
}
```

`cache_agrees` compares the value in the cached snapshot with a fresh computation. It answers support's second question, "is it the cache?", without anyone opening Redis.

---

## 6. Redis caching, invalidation and expiring overrides

### 6.1 Structure

| key | value | TTL |
|---|---|---|
| `ent:{tenant_id}` | `{"stamp": [version, token, plan], "until": iso, "features": {key: {...}}, "flags": [on-flag keys]}`, about 2–4 KB | `min(5 min, until − now)` |
| `ent:catalog` | a random hex token | none |

**Read path:** one `MGET ent:catalog ent:{tenant}`.
1. It is a hit when `snapshot.stamp == [tenants.entitlements_version, token, tenants.plan]` and `now < until`.
2. Otherwise the service recomputes (about 6 small queries), writes the snapshot back, and keeps it for the rest of the request.
3. Several checks in one request share one read.

**What each change does:**

| Change | Mechanism | Live after |
|---|---|---|
| Plan change (subscribe, cancel, trial→read_only, admin edit, SQLAdmin) | **Nothing.** `tenants.plan` is part of the stamp, and the middleware loads the tenant row fresh on every request | the next request |
| Override created or revoked; flag target added or removed | `bump_tenant()` **in the same transaction** as the change | the next request |
| Add-on bought, cancelled or renewed; app installed or uninstalled | `bump_tenant()` in `app_billing.subscribe/cancel/renew_due` and the install routes | the next request |
| Add-on lapses, override expires, scheduled override starts | nothing: `until` is capped at that boundary, and the resolver filters by time | the boundary itself |
| Catalog, plan grant, kill switch, flag switch or percentage | `bump_catalog()` **after the commit**: a new random token | milliseconds after the commit |
| A write path that forgot to bump (a bug) | the 5-minute TTL | ≤ 5 minutes |

### 6.2 Why stamps and not DELETE

Delete-on-write has a race:
1. Request A reads the old overrides.
2. Admin B commits a change and deletes the key.
3. Request A writes its old snapshot back. It stays stale until the TTL.

With stamps, A's snapshot carries the version A read *before* it read the data. The next reader sees a newer version, knows A's snapshot is stale, and recomputes.

Two ordering rules make this sound. Both are already in the code:
- **Readers take the stamp before they read the data.** Under READ COMMITTED, data read later is at least as new as the version, so a stale snapshot can never carry a fresh stamp.
- **Per-tenant writers bump inside the transaction; catalog writers rotate the token after the commit.** If the token were rotated before the commit, a reader could cache old data under the new token.

The token is random rather than a counter. After a Redis restart it can never reissue a value that an old snapshot carries.

Snapshots are not kept in process memory. That is the `plan_limits` split-brain again (B5): N workers, N different truths.

### 6.3 Expiring overrides

- **Expiry is a WHERE clause, not a cron job.** The resolver ignores overrides and add-ons outside their window, and the snapshot's `until` is capped at the next start or end. The value flips at the boundary with no job running.
- **The rows stay put.** An expired override is still there (`revoked_at IS NULL`, `expires_at` in the past), so history keeps it. Creating a new override for the same feature revokes the old one.
- **V2 adds an optional nightly job, only for people:**
  - a merchant notice ("Advanced analytics trial ends in 3 days")
  - an admin "expiring soon" list
  - an `entitlement.override.expired` audit row

  Correctness never depends on that job.

### 6.4 When Redis is down

`RedisCacheService` already turns every Redis error into a miss. Each request then computes from Postgres: the answer is still correct, just slower. `bump_catalog` fails loudly (`logger.alert`) so the admin who flipped the switch knows caches may lag by up to 5 minutes.

---

## 7. Limits and usage: hard, soft, overage, upgrade

| Enforcement | Behaviour | Use for |
|---|---|---|
| hard, count | `check_quota()`: take an advisory lock on (tenant, feature), COUNT, then refuse with 402 if over | Merchant-initiated creates: products, staff, stores, locations, partner apps |
| hard, counter | `consume()`: one atomic `INSERT … ON CONFLICT DO UPDATE … WHERE used + n <= limit RETURNING used`; no row back means refused | Merchant-paid allowances: WhatsApp messages, automation runs |
| soft | Allow. Record it and notify once per period; in V2, bill the overage | **Anything a customer triggers.** Storefront orders must never fail because of a merchant's plan |
| upgrade required | 403 `FEATURE_NOT_AVAILABLE` or 402 `PLAN_LIMIT_EXCEEDED`, both with `available_via` | The hub's upgrade dialog (§11) |

**Rules:**
- **Enforce in the shared write path, not in a route dependency.** This is what fixes B1 and B2. Call `check_quota(tenant, "products", adding=len(rows))` once in the product-creation use case, and have hub create, CSV import, the agent tool and social import all go through it. A per-route dependency guards one door of a house with four.
- **Consume in the same transaction as the durable write** (the message-log row, the automation-run row). A rollback then takes the count back out: the test in Appendix A checks this. Redis counters can't do that. That is why Redis is only for high-frequency, non-billed meters.
- **For soft limits counted from rows** (`orders_per_month`), don't check first. A post-commit handler on `OrderCreatedEvent` (the EventBus already defers dispatch until after commit) compares `usage()` with the limit. The first time the limit is crossed in a period, it calls `emit_notification()`. Deduplicate with `SET ent:notified:{tenant}:{feature}:{period} NX`.
- **Overage billing (V2) lives in billing.**
  - A soft `consume()` over the limit emits a `UsageOverage` event.
  - The billing handler debits the wallet ledger with a new `usage_overage` kind and the idempotency key `overage:{tenant}:{feature}:{period}:{used}`.
  - Prices live in billing, never in entitlement rows.
- **Downgrades never delete data.** A merchant with 300 products who drops to a 100-product plan keeps all 300. Only the 301st create is refused, because `check_quota` counts the real rows.
- **Response when a limit is reached** (HTTP 402; this is the existing `PLAN_LIMIT_EXCEEDED` code with its details extended):

```json
{"success": false, "error": {"code": "PLAN_LIMIT_EXCEEDED",
  "message": "Plan limit reached: your starter plan allows 100 products (currently at 100). Upgrade to continue.",
  "details": {"feature": "products", "resource": "products", "limit": 100, "current": 100,
              "plan": "starter", "resets_at": null, "upgrade_required": true,
              "available_via": ["pro"], "upgrade_to": "pro"}}}
```

Keep the existing code and status. Clients and the admin already know them, and renaming them gains nothing. Clients should branch on `error.code`, never on the HTTP status.

---

## 8. FastAPI integration

### 8.1 Dependencies (`src/api/dependencies/entitlements.py`)

```python
"""FastAPI entry points to EntitlementService."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.application.services.entitlement_service import EntitlementService
from src.core.entities.store import Store
from src.core.exceptions import FeatureNotReleasedError
from src.infrastructure.database.models.public.tenant import TenantModel


def get_entitlements(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> EntitlementService:
    """One service per request, so every check in it shares one snapshot."""
    service = getattr(request.state, "entitlements", None)
    if service is None:
        service = request.state.entitlements = EntitlementService(db)
    return service


async def store_tenant(
    request: Request,
    store: Annotated[Store, Depends(verify_store_ownership)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantModel:
    """The tenant that owns the path's store. The path is authoritative (it
    was ownership-checked); the middleware's row is reused when it matches,
    which is every normal hub request, so the version check costs nothing."""
    current = getattr(request.state, "tenant", None)
    if current is not None and current.id == store.tenant_id:
        return current
    tenant = await db.get(TenantModel, store.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Store not found")
    return tenant


def require_feature(key: str):
    async def check(
        tenant: Annotated[TenantModel, Depends(store_tenant)],
        ents: Annotated[EntitlementService, Depends(get_entitlements)],
    ) -> None:
        await ents.require(tenant, key)

    return Depends(check)


def require_flag(key: str):
    async def check(
        tenant: Annotated[TenantModel, Depends(store_tenant)],
        ents: Annotated[EntitlementService, Depends(get_entitlements)],
    ) -> None:
        if not await ents.flag(tenant, key):
            raise FeatureNotReleasedError(key)

    return Depends(check)
```

It is a dependency, not middleware, because resolution should be lazy (§2.11).

The tenant comes from the ownership-checked path, never from the request body. The `X-Tenant-Id` header is only a hint that lets the dependency reuse the row the middleware already loaded.

### 8.2 Error handlers (additions to `src/api/middleware/error_handler.py`)

```python
    @app.exception_handler(FeatureNotAvailableError)
    async def feature_not_available_handler(
        request: Request, exc: FeatureNotAvailableError
    ):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_error_body(
                "FEATURE_NOT_AVAILABLE",
                str(exc),
                {
                    "feature": exc.feature,
                    "reason": exc.reason,
                    "upgrade_required": exc.upgrade_required,
                    "available_via": exc.available_via,
                },
            ),
        )

    @app.exception_handler(FeatureDisabledError)
    async def feature_disabled_handler(request: Request, exc: FeatureDisabledError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "300"},
            content=_error_body(
                "FEATURE_TEMPORARILY_DISABLED",
                str(exc),
                {"feature": exc.feature, "retryable": True},
            ),
        )

    @app.exception_handler(FeatureNotReleasedError)
    async def feature_not_released_handler(
        request: Request, exc: FeatureNotReleasedError
    ):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_error_body("FEATURE_NOT_RELEASED", "Not found"),
        )
```

In the existing `plan_limit_handler`, add `"feature": exc.feature`, `"resets_at": exc.resets_at`, `"available_via": exc.available_via` and `"upgrade_required": bool(exc.available_via)` to `details`.

| Code | HTTP | When | Upsell? |
|---|---|---|---|
| `FEATURE_NOT_AVAILABLE` | 403 | not entitled: `not_in_plan`, `blocked` or `unknown_feature` | only when `upgrade_required` |
| `PLAN_LIMIT_EXCEEDED` | 402 | a hard limit was hit | when `available_via` is not empty |
| `FEATURE_TEMPORARILY_DISABLED` | 503 + `Retry-After` | the kill switch is on | never: it isn't the merchant's doing |
| `FEATURE_NOT_RELEASED` | 404 | the flag is off for this tenant | never |

### 8.3 Backend authorization examples

```python
# 1. A boolean feature on a route.
@router.get("/insights", dependencies=[require_feature("advanced_analytics")])
async def advanced_insights(...): ...

# 2. A release flag and an entitlement: 404 if unreleased, 403 if not entitled.
@router.post(
    "/locations",
    dependencies=[require_flag("multi_warehouse_v1"), require_feature("multi_warehouse")],
)
async def create_location(...): ...

# 3. A resource quota in the shared write path (fixes B2 for every caller).
class CreateProductsUseCase:
    async def execute(self, tenant: TenantModel, rows: list[ProductIn]) -> list[Product]:
        await self.entitlements.check_quota(tenant, "products", adding=len(rows))
        return await self.products.bulk_create(rows)   # same transaction as the lock

# 4. A metered allowance, consumed in the same transaction as the durable record.
async def send_template(db, tenant, message):
    ents = EntitlementService(db)
    await ents.consume(tenant, "whatsapp_messages")      # 402 when exhausted (hard)
    db.add(MessageLogModel(...))
    await provider.send(message)                         # failure -> rollback un-counts

# 5. Reading a limit without enforcing it (UI copy, admin reports).
limit = await ents.limit(tenant, "orders_per_month")   # 500 or "unlimited"

# 6. Celery: check again when the work runs. The tenant may have lost the
#    feature since the job was queued.
async with AsyncSessionLocal() as db:
    tenant = await db.get(TenantModel, tenant_id)
    if not await EntitlementService(db).has(tenant, "automation"):
        logger.insight("automation_skipped_not_entitled", tenant_id=str(tenant_id))
        return
```

Staff permissions (RBAC, `require_permissions`) are a separate check. The entitlement says the store may use analytics. The permission says this staff member may. Routes need both.

---

## 9. Percentage rollouts

`bucket = sha256("{flag_key}:{tenant_id}")[:8] mod 10000`. A tenant is in the rollout when `bucket < rollout_percent × 100`.

**Properties** (§15 tests each one):

| Property | Why it holds |
|---|---|
| Deterministic across workers, languages and restarts | It is sha256, not Python's `hash()`, which is salted per process |
| Sticky and monotonic | Raising 10% → 25% only adds tenants; nobody flips from on to off |
| Independent per flag | The flag key is part of the hash, so two 20% rollouts overlap on about 4% of tenants, not 20% (checked over 20,000 tenants) |
| 0.01% granularity ready | Moving from percent to basis points changes the comparison, not the assignment |

**Rejected alternatives:**

| Alternative | Why not |
|---|---|
| `tenant_id % 100` | The same tenants would get every beta |
| `random()` per request | A tenant would flicker between versions |
| Python `hash()` | A different bucket on every worker |

**Rollout playbook:**
1. Target internal tenants (`tenants.is_internal=true` already exists).
2. Target 3–5 friendly merchants.
3. 5%, then 25%, then 100%.
4. Delete the flag and its code path within 2 weeks of reaching 100%. The admin list sorts by age and marks flags stuck at 0% or 100% for more than 30 days.

---

## 10. Add-ons, subscription changes, PAYG

### 10.1 Add-ons and future apps

| Situation | How it is expressed | What feature code sees |
|---|---|---|
| Included in Pro | a `plan_entitlements` row (`pro`, `cod_shield`, true) | `require("cod_shield")` |
| Sold separately | a NUMU App with a recurring price, bought via `app_billing.subscribe` (wallet). Grants come from `plan_entitlements` rows with `plan_key='addon:cod_shield'` | the same |
| Given free to one merchant | an override, `source='sales'` or `'support'`, with an expiry | the same |
| Beta | a flag (release) plus an override or plan grant (entitlement) | the same, plus `require_flag` |
| Usage-based | a limit feature with a counter and soft enforcement; overage is priced in billing (V2) | `consume()` |
| Own limits | more limit keys in the same `addon:<slug>` bundle | `limit()` |
| Disabled during an incident | the kill switch | 503, no upsell |

- **Active add-ons come from `app_subscriptions`.** An add-on counts while it `covers()` the current time. The resolver uses the same rule through `coverage_end()`, a two-line extraction that `covers()` then calls (§20.2), so the two can never disagree about the 3-day grace.
- **Don't charge for what the plan includes.** Before charging, `app_billing.subscribe` asks `has(tenant, feature)`, excluding add-on sources. If the plan already grants the feature, the app installs free and shows "Included in your plan".
- **The NUMU App install switch stays merchant configuration.** Installed-and-enabled is the merchant's own on/off; the entitlement decides whether they are allowed to switch it on. `app_enabled()` keeps doing its job.

### 10.2 How subscription changes affect entitlements

| Event | Effect | Mechanism |
|---|---|---|
| Upgrade (starter → pro) | The new grants apply on the next request | `plan` is in the stamp |
| Downgrade | Lost features lock on the next request. Data is kept; growth past the new limits is refused | stamp; `check_quota` counts the real rows |
| Renewal | Nothing | `plan` doesn't change |
| Failed renewal / past_due | Nothing. This is still a paying customer: the 2026-09-07 lock incident | lifecycle is not an entitlement |
| Trial → read_only | Entitlements unchanged; the write lock belongs to the lifecycle | `is_read_only`, `storefront_lock` |
| Cancel at period end | The plan stays until billing flips it | stamp |
| Add-on bought or cancelled | Immediate | `bump_tenant` in `app_billing` |
| Add-on lapses | At `coverage_end` (period end plus 3 days' grace) | the snapshot's `until` |
| Admin changes what Pro includes | Every Pro tenant, on their next request | catalog token. The UI shows how many tenants are affected; *reductions* require a reason plus either grandfather overrides or a new plan key (`pro_2027`, V2) |
| Price change | Not an entitlement | `PLAN_LIMITS` / billing |

### 10.3 PAYG

- **`payg` is just another `plan_key`,** with its own grants (Starter-like today). Nothing in the resolver treats it specially.
- **Commission is billing.** It stays in `PLAN_LIMITS.commission_bps` and `merchant_wallets.commission_bps_override`. Don't move it into entitlements: it is a price, not a permission.
- **The wallet checkout gate, the go-live gate and the billing lock are account state.** They stay in `wallet_service` and `storefront_lock`.
- **PAYG merchants buy add-ons from the wallet.** That already works through `app_billing`.
- **V2 usage pricing:** soft limits plus wallet-debited overage (§7). This is where PAYG and metering meet: "pay as you grow" becomes "each meter has a free tier per plan; above it, the wallet pays per unit".

---

## 11. Merchant hub: API contract and React integration

### 11.1 Contract

`GET /api/v1/stores/{store_id}/entitlements` answers from cache and never counts anything:

```json
{"success": true, "data": {
  "plan": "starter",
  "features": {
    "advanced_analytics": {"value": true, "available": true, "source": "override",
                           "expires_at": "2026-10-24T12:00:00+00:00", "reason": null,
                           "kind": "boolean", "period": null},
    "products":           {"value": 100, "available": true, "source": "plan",
                           "expires_at": null, "reason": null, "kind": "limit", "period": null},
    "multi_warehouse":    {"value": false, "available": false, "source": "plan",
                           "expires_at": null, "reason": "not_in_plan", "kind": "boolean",
                           "period": null}
  },
  "flags": ["checkout_v2"]
}}
```

- **`flags` lists only the flags that are on.** A merchant who isn't in the `new_theme_builder` beta never learns that it exists.
- **`source_id` is left out.** Override IDs are internal.

`GET /api/v1/stores/{store_id}/entitlements/usage` does count. The Billing page uses it, and so do limit banners, on demand:

```json
{"success": true, "data": {"usage": [
  {"feature": "products", "limit": 100, "used": 67, "remaining": 33, "resets_at": null},
  {"feature": "orders_per_month", "limit": "unlimited", "used": 1234,
   "remaining": "unlimited", "resets_at": "2026-10-01T00:00:00+00:00"}
]}}
```

This replaces `GET /stores/{id}/plan/usage`. `GET /stores/{id}/plan/limits` (the public plan matrix) is rebuilt from `plan_entitlements`, and that fixes the Billing page's hardcoded `PLAN_FEATURES`, whose comment says it "MUST stay in step with PLAN_LIMITS".

### 11.2 React

**Fix these first. Without them no gate works (B8, B9):**
1. In `services/api.ts`, stop discarding 403 bodies. Throw `apiErrorFromResponse(res.status, body)`, as the other branches do.
2. In `lib/api-error.ts`, give `ApiError` the error code and details from the response body:
   ```ts
   get code(): string | undefined { return (this.body as ErrBody | null)?.error?.code; }
   get details(): Record<string, unknown> | undefined { return (this.body as ErrBody | null)?.error?.details; }
   ```
3. Fix the readers that look at `body.detail.*`: the WhatsApp 409, `AppSubscriptionCard`'s 402, and the customizer's `stale_etag`.

**Data.** This is React Query keyed by store, not AuthContext. `/auth/me` doesn't refetch when the merchant switches store; the query key does.

```ts
// src/services/entitlementsApi.ts
import { apiClient } from "./api";

export type Limit = number | "unlimited";
export interface FeatureState {
  value: boolean | Limit;
  available: boolean;
  source: "plan" | "addon" | "override" | "default";
  expires_at: string | null;
  reason: "not_in_plan" | "blocked" | "disabled_globally" | "unknown_feature" | null;
  kind: "boolean" | "limit";
  period: "day" | "month" | null;
}
export interface Entitlements {
  plan: string;
  features: Record<string, FeatureState>;
  flags: string[];
}

export const entitlementKeys = {
  all: ["entitlements"] as const,
  store: (storeId: string) => ["entitlements", storeId] as const,
};

export const getEntitlements = (storeId: string) =>
  apiClient<Entitlements>(`/stores/${storeId}/entitlements`);
```

```ts
// src/hooks/useEntitlements.ts
import { useQuery } from "@tanstack/react-query";
import { useDashboardStore } from "@/contexts/StoreContext";
import { entitlementKeys, getEntitlements, type Limit } from "@/services/entitlementsApi";

export function useEntitlements() {
  const storeId = useDashboardStore().currentStore?.id;
  const query = useQuery({
    queryKey: entitlementKeys.store(storeId ?? "none"),
    queryFn: () => getEntitlements(storeId!),
    enabled: !!storeId,
    staleTime: 60_000,
  });
  const data = query.data;
  return {
    ready: !!data,
    failed: query.isError,
    plan: data?.plan,
    has: (key: string) => data?.features[key]?.available ?? false,
    limit: (key: string) => data?.features[key]?.value as Limit | undefined,
    feature: (key: string) => data?.features[key],
    flag: (key: string) => data?.flags.includes(key) ?? false,
  };
}
```

```tsx
// src/components/FeatureGate.tsx
import type { ReactNode } from "react";
import { useEntitlements } from "@/hooks/useEntitlements";
import { UpgradeCard } from "@/components/billing/UpgradeCard";

export function FeatureGate({ feature, flag, fallback, children }: {
  feature?: string; flag?: string; fallback?: ReactNode; children: ReactNode;
}) {
  const ents = useEntitlements();
  if (!ents.ready) {
    // UX only; the API enforces. A failed fetch must not lock a paying
    // merchant out, but must not reveal an unreleased feature either.
    return ents.failed && !flag ? <>{children}</> : null;
  }
  if (flag && !ents.flag(flag)) return null;               // unreleased: render nothing
  if (feature && !ents.has(feature)) return <>{fallback ?? <UpgradeCard feature={feature} />}</>;
  return <>{children}</>;
}
```

**The upgrade dialog, from any 402 or 403:**
- **A small Zustand store.** Zustand is already installed: `useUpgradeDialog = create(set => ({details: null, open: d => set({details: d}), close: () => set({details: null})}))`.
- **One branch at the top of the existing `MutationCache.onError` in `App.tsx`:**
  ```ts
  if (error instanceof ApiError && (error.code === "FEATURE_NOT_AVAILABLE" || error.code === "PLAN_LIMIT_EXCEEDED")) {
    queryClient.invalidateQueries({ queryKey: entitlementKeys.all }); // our view was stale
    useUpgradeDialog.getState().open(error.details as UpgradeDetails);
    return;
  }
  ```
- **`<UpgradeDialog/>` is mounted in `DashboardLayout`,** which is inside `BrowserRouter` and `StoreProvider`. Don't mount it where `TrialPaywallProvider` sits: that is above the router, so it can't navigate.
- **Its content:**
  - It uses `planLabel.ts` as the single source of plan names; three maps disagree today.
  - Its copy depends on `reason`.
  - Its CTA goes to `/billing?plan=pro`, or to `/apps/<slug>` for `addon:*`.

**Other hub changes:**
- **Nav.** `NavItemGate` gets an optional `feature` prop. When the merchant isn't entitled it shows a lock badge (the feature can still be discovered and bought); when the flag is off it hides the item.
- **After any billing action,** call `invalidateQueries({ queryKey: entitlementKeys.all })` instead of `window.location.reload()`.
- **Replace:** `themePlan.canUnlockTheme`/`TIER_ORDER`, the scattered `tenant?.plan === "payg"` checks that are really feature checks, and the fake plan badge in `StoreSettings` (it is derived from currency).
- **`useFeatureFlag(name)`** becomes a one-line wrapper over `useEntitlements().flag(name)`.

---

## 12. Storefront

**V1 needs nothing.** The storefront doesn't gate anything by plan today. Features that load through backend calls (reviews, bundles, apps, OTP) disappear when the backend returns empty or 404; the theme SDK's `useApp()` already treats a 404 as `{available: false}`.

**When a paid feature is rendered by the storefront itself** (for example, removing the four hardcoded "Powered by NUMU" strings for Pro):
- **Add a small block to the public store payload:** `entitlements: {remove_branding: true}`.
  - Booleans only.
  - Each value is already "the merchant's setting AND entitled".
  - Never plan names or limits: this payload is public.
- **Revalidate both cache tags** (`store-{subdomain}` and `store-{host}`) from the same paths that bump the version. Otherwise a downgrade shows up only after the 60-second ISR window.
- **Fix B12 at the same time:** stop sending `tenant_feature_flags`, and send only what the storefront actually reads.

---

## 13. Internal admin UI and audit

### 13.1 Screens

The patterns come from the admin sweep. Clone ds pages, not the legacy shadcn ones.

**Features** (`/features`): clone `Merchants.tsx`, with MetricCards, a FilterBar and a DataTable.

```
Features                             [search…]   All · Limits · Killed · With overrides
┌───────────────────────┬─────────┬────────────────────────────────┬───────────┬──────────┐
│ Feature               │ Kind    │ Plans                          │ Overrides │ State    │
├───────────────────────┼─────────┼────────────────────────────────┼───────────┼──────────┤
│ Multiple locations    │ boolean │ pro · developer · enterprise   │ 3 live    │ ● On     │
│ Products              │ limit   │ starter 100 · payg 100 · pro ∞ │ 1 live    │ ● On     │
│ WhatsApp automation   │ boolean │ addon:whatsapp                 │ 0         │ ◌ KILLED │
└───────────────────────┴─────────┴────────────────────────────────┴───────────┴──────────┘
```

**Feature detail** (`/features/:key`): clone `MerchantDetail.tsx`, with ds `Tabs` passed through the `tabs` prop of `DashboardLayout` (the idiom `SubscriptionPayments` uses).

```
Multiple locations   multi_warehouse · boolean · logistics      ● Enabled   [Disable globally…]
Overview | Plans & add-ons | Overrides (3) | Releases | History

Plans & add-ons                                               [Edit…] reason required
  demo ○  trial ○  free ○  beta ○  starter ○  payg ○  pro ●  developer ●  enterprise ●
  addon:multi_location ●
  Editing starter → on shows: "Affects 214 tenants on starter."

Overrides                                                      [+ Add override]
  Pixel Print   ● on   beta      "early access #88"   → 2026-10-24   yahia   [Revoke]
  Merchant X    ○ off  contract  "abuse case #12"     no expiry      yahia   [Revoke]
  ⚠ Merchant Y  4      support   "ticket 9001"        → 2026-11-01   ← below plan value (10)

Releases (flags with feature_key = multi_warehouse)
  multi_warehouse_v1   ● ON · 10% + 4 targeted · owner yahia · 12 days   [Open]

History  (ds AuditTimeline over audit_logs where resource = feature:multi_warehouse)
  2026-09-20 14:02  yahia  plan grant  pro: off → on        "Pro launch"
```

**Rules for the destructive actions:**
- **Disable globally** uses `ConfirmDialog` with a typed confirm phrase, the 2FA step-up (`require_admin_2fa`, as capabilities and apps already do), and a required reason. While any feature is killed, a banner shows on every admin page; the Capabilities page has the pattern.
- **Plan grant edits** show the number of affected tenants before saving. A *reduction* has to name either a grandfather action or a new plan key.

**Flags** (`/flags`): one list and one detail view.

```
Flags                                                             sorted by age
  checkout_v2          ● ON  25% · 3 targets    owner yahia   12 days
  multi_warehouse_v1   ● ON   0% · 4 targets    owner yahia   12 days
  analytics_v2         ○ OFF                    owner —       94 days   ⚠ stale
Detail: master switch (2FA to turn OFF) · percent slider 0–100 with 1/5/25/50/100 presets ·
        targets table (+ merchant, on/off, expires, reason) · "Evaluate for merchant…" →
        "on: rollout, bucket 1834 < 2500" · history
```

**Merchant detail → Entitlements panel.** Generalize `components/merchants/ApiAccessPanel.tsx`, which already shows plan-includes / granted / effective. Mount it the same way, with its own `useQuery`:
- It has one row per feature, with a source badge:
  - `Plan: starter`
  - `Add-on: cod_shield · until 1 Oct`
  - `Override (support) · until 12 Oct`
  - `Default`
  - `KILLED`
- Clicking a row opens the **Explain drawer**, which answers "why does merchant X have Y?" without anyone opening the database:

```
Why does Pixel Print have Multiple locations?               ✓ available · source: override
 1  Kill switch       on
 2  Override          ✓ on · beta · "early access #88" · yahia · 24 Sep → 24 Oct   ← wins
 3  Plan: starter     ✗ off                                                      shadowed
 4  Add-on            multi_location · lapsed 1 Sep                              not live
 5  Default           off
 Cache agrees ✓                                          [Revoke override]  [Add override]
Flags for this merchant:  checkout_v2 on (rollout 1834 < 2500) · multi_warehouse_v1 on (targeted)
Usage:  products 67 / 100 · orders this month 412 / unlimited
```

**Environment.** The admin's existing environment switcher (prod/stage/test in `lib/env.ts`) is the environment control. There is nothing to build.

**Registering the pages.** One `ADMIN_NAV` item each for Features and Flags, plus their `<Route>`s in `App.tsx`. That single entry also covers the command palette and the breadcrumbs.

**Remove the duplicate plan editor.** `PlanLimits.tsx` and the Plans tab in `SubscriptionPayments` edit the same `/admin/plan-limits` endpoint under different cache keys. After migration that page edits **prices only**. The entitlement matrix lives on the Features screen.

**Admin API** (`/api/v1/admin/…`, `require_admin`; 🔐 means 2FA step-up):

| Method | Path | Body / notes |
|---|---|---|
| GET | `entitlements/features` | list, with live override counts and the plans granting each feature |
| GET | `entitlements/features/{key}` | grants by plan, override counts, releases |
| PATCH | `entitlements/features/{key}` | name, name_ar, description, enforcement; reason |
| POST 🔐 | `entitlements/features/{key}/kill-switch` | `{enabled, reason}` |
| PUT 🔐 | `entitlements/features/{key}/grants/{plan_key}` | `{value, reason}` → `{affected_tenants}`; `plan_key` must be in `PLAN_LIMITS` or start with `addon:` |
| GET | `entitlements/features/{key}/overrides` | `?status=live\|expired\|revoked`, paged |
| POST | `entitlements/tenants/{tenant_id}/overrides` | `{feature_key, value, source, reason, starts_at?, expires_at?}` (§20.3) |
| POST | `entitlements/overrides/{id}/revoke` | `{reason}` |
| GET | `entitlements/tenants/{tenant_id}` | fresh snapshot, flags with why, usage |
| GET | `entitlements/tenants/{tenant_id}/explain/{key}` | §5 output |
| GET / POST | `flags` | list (with age and target counts) / create `{key, description, owner, feature_key?}` |
| PATCH | `flags/{key}` | `{enabled?, rollout_percent?, reason}`; 🔐 when switching off |
| PUT / DELETE | `flags/{key}/targets/{tenant_id}` | `{enabled, expires_at?, reason}`; bumps the tenant |
| GET | `flags/{key}/evaluate?tenant_id=` | `{on, why, bucket}` |
| GET | `entitlements/audit` | `?feature=&flag=&tenant_id=&before=` |

### 13.2 Audit

Reuse `audit_logs` and `AuditService.log(...)`, in the **same transaction** as the change. That way the change and its record commit together or not at all.

| event_type | resource | recorded |
|---|---|---|
| `entitlement.feature.kill_switch` | `feature:<key>` | `old_value`/`new_value` `{is_enabled}`, reason, severity `warning` |
| `entitlement.plan_grant.update` | `feature:<key>` | `{plan_key, old, new, reason, affected_tenants}` |
| `entitlement.override.create` / `.revoke` | `feature:<key>` + `tenant_id` | the old and new override, source, reason, expires_at |
| `flag.update` | `flag:<key>` | old and new `{enabled, rollout_percent}`, reason |
| `flag.target.set` / `.remove` | `flag:<key>` + `tenant_id` | enabled, expires_at, reason |

- **Who, when, previous value, reason, expiry.** These come from `user_id`, `created_at`, `details.old_value` and `details.reason`, and the expiry is in `new_value`. The migration adds `ix_audit_logs_resource (resource_type, resource_id, created_at)` so the History tabs don't scan auth and order events.
- **An expiry writes no row, because nothing happened.** The History view derives "expired on X" from the override row itself.
- **The ds `AuditTimeline` is ready and unused.** Its `meta` field is designed for "before → after". Point Home's dead "Audit log" button at `/audit`, a filtered list over the same endpoint, in V2.

---

## 14. Migrating away from hardcoded plan checks

Each phase ships on its own. **Count the affected tenants before any behaviour change.** The storefront lock closed nine live stores on 2026-09-07 because a warning written into a PR description was never acted on.

**Phase 0: add the new system, change no behaviour.**
- The tables, `entitlements_version`, and the seed (§20.4):
  - `PLAN_LIMITS` plus prod's `platform_config.plan_limits` edits
  - `-1` → `"unlimited"`
  - `beta` = trial, which keeps today's fallback behaviour explicit
- The `feature_flags.api_access=true` grants become `migration` overrides.
- Ship the service, dependencies, merchant endpoints and error handlers.
- **Shadow mode for one week.** `PlanLimitService` computes both answers and logs `entitlements_shadow_mismatch` whenever they differ. Zero mismatches is the gate for Phase 1.

**Phase 1: swap the internals; behaviour is identical.**
- `PlanLimitService.check_product_limit` → `check_quota(tenant, "products")`, on the same routes.
- `check_order_limit` → the same routes, using `usage()` against `limit()`.
- `stores.py:257` `get_plan_features(primary.plan).max_stores` → `limit(primary, "stores")`.
- `app_oauth.py:245` `_check_app_cap` → `limit(tenant, "partner_apps")`.
- `api_access.py` `_decide` → `has(tenant, "api_access")`.
- `CapabilityService.resolve` plan floor → `has(tenant, key)` for `multi_warehouse` and `product_subscriptions`.
- Delete `_PLAN_RANK` and `_UPGRADE_MAP`; `available_via` replaces them.
- Test gate: the seed-parity test in §15.

**Phase 2: deliberate behaviour changes, one decision each (§22).**
- Move the product quota into the shared creation path (B2). **Count first** the tenants already over their product cap: Qandeel and pixel print were bulk-imported past caps. Grandfather them with overrides.
- Orders become soft everywhere (D2). The hub stops hard-blocking, and the `OrderCreated` handler notifies instead (B1).
- Webhooks: fold them into `api_access` (D3).
- Every never-enforced field you decide to enforce (D4) gets a grandfather query before `require()` is wired. For example:

```sql
-- Tenants that would lose multi_warehouse when enforcement lands.
INSERT INTO public.entitlement_overrides (tenant_id, feature_key, value, source, reason)
SELECT s.tenant_id, 'multi_warehouse', 'true', 'migration', 'grandfathered 2026-10: had >1 location'
FROM public.locations l
JOIN public.stores s ON s.id = l.store_id
JOIN public.tenants t ON t.id = s.tenant_id
WHERE t.plan NOT IN ('pro', 'developer', 'enterprise') AND l.is_active
GROUP BY s.tenant_id HAVING count(*) > 1
ON CONFLICT DO NOTHING;
```

Run the same query as a plain `SELECT count(*)` first and read the number before you ship.

**Phase 3: delete.**
- The feature fields of `PlanFeatures`: `max_*`, `*_enabled` and `max_partner_apps`, all covered by the seed. Keep `display_name`, the prices and `commission_bps`.
- `plan_limit_service.py`, `api/dependencies/plan.py`, and the dead gates listed in §1.1.
- The feature half of `/admin/plan-limits`, and the admin's duplicate plan editor.
- Once the fields are gone, mypy flags any leftover `get_plan_features(x).max_products`. **The type checker is the ratchet.** You need no custom lint.

**Phase 4: flags.**
- `ff_numu_apps` becomes a `feature_flags` row (`numu_apps`) plus targets for the tenants that have it today, and `numu_apps.py` reads `ents.flag()`.
- Move each `settings.ff_*` flag the next time you touch it.
- `tenants.feature_flags` shrinks to `golive_exempt` and `wallet_checkout_gate`, which are billing's (V2: real columns). `/auth/me` then stops merging it.

**Rule of thumb for what may keep reading `tenants.plan`:**
- Billing flows: payg activation, `INSTAPAY_PAYABLE_PLANS`, renewals.
- Plan identity: developer stores refuse real orders; demo and developer stores are noindex.
- Reports: MRR.

**New features** (advanced analytics, automation, abandoned cart, COD Shield, reviews, bundles) each ship in one migration with their catalog row, their grants and a grandfather query. Abandoned-cart recovery is on for everyone today, so making it Pro-only is decision D5.

---

## 15. Testing strategy

| Layer | What | Where it runs |
|---|---|---|
| Pure rules | Precedence, merging, the override window, the kill switch, bool/int strictness, `next_change`, bucket stability, distribution and independence, flag order. **The 13 tests below were run and pass.** | unit, SQLite suite |
| Seed parity | For every plan in `PLAN_LIMITS` and every migrated field, the seeded value matches the old value (`-1` ↔ `"unlimited"`), and capability rank matches the `multi_warehouse` grants. **This was run: exact match, 90 rows, 9 plans.** It is Phase 1's safety net | unit |
| Service | Miss then hit; override plus bump; a plan change with no bump; the kill switch; add-on stacking; explain layers; the hard counter; the quota lock; a flag target. **These 8 scenarios were run** against the real models, Postgres 15 and Redis | integration (needs Postgres: `ON CONFLICT … WHERE`, advisory locks) |
| Concurrency | 60 parallel `consume()` calls against a limit of 50 leave exactly 50. **This was run** | integration |
| Contracts | Snapshot tests of the four error bodies; the hub depends on them | API tests |
| Catalog consistency | Every key used in `require_feature("…")` or `ents.*(…, "…")` exists in the seed. A grep-based test | unit |
| Shadow | One week in production: `entitlements_shadow_mismatch` must be 0 | prod logs |
| Hub | `FeatureGate` with a mocked `useEntitlements`, following the pattern of the `TrialBanner` test | vitest |

`tests/unit/test_entitlements.py`:

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.core.entitlements import (
    UNLIMITED,
    Feature,
    Flag,
    FlagTarget,
    Grant,
    bucket,
    check_value,
    flag_on,
    next_change,
    resolve,
)

NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
DAY = timedelta(days=1)
ANALYTICS = Feature("advanced_analytics", "boolean", False)
STAFF = Feature("staff_accounts", "limit", 1)


def plan(value, key="starter"):
    return Grant("plan", key, value)


def addon(value, slug="analytics_plus", ends=NOW + 10 * DAY):
    return Grant("addon", slug, value, expires_at=ends)


def override(value, starts=None, ends=None):
    return Grant("override", "ovr-1", value, starts_at=starts, expires_at=ends)


def test_plan_grants():
    r = resolve(ANALYTICS, bundles=[plan(True, "pro")], override=None, now=NOW)
    assert (r.available, r.source, r.source_id, r.expires_at) == (True, "plan", "pro", None)


def test_override_denies_and_shows_what_it_shadowed():
    r = resolve(ANALYTICS, bundles=[plan(True, "pro")], override=override(False), now=NOW)
    assert (r.available, r.reason, r.source) == (False, "blocked", "override")
    assert r.shadowed == (plan(True, "pro"),)


def test_override_only_counts_inside_its_window():
    for window in (override(True, ends=NOW), override(True, starts=NOW + DAY)):
        r = resolve(ANALYTICS, bundles=[plan(False)], override=window, now=NOW)
        assert (r.available, r.source, r.reason) == (False, "plan", "not_in_plan")
    live = override(True, starts=NOW - DAY, ends=NOW + 30 * DAY)
    r = resolve(ANALYTICS, bundles=[plan(False)], override=live, now=NOW)
    assert (r.available, r.source, r.expires_at) == (True, "override", NOW + 30 * DAY)


def test_addon_grants_until_it_ends_unless_the_plan_also_does():
    r = resolve(ANALYTICS, bundles=[plan(False), addon(True)], override=None, now=NOW)
    assert (r.available, r.source, r.expires_at) == (True, "addon", NOW + 10 * DAY)
    r = resolve(ANALYTICS, bundles=[plan(True, "pro"), addon(True)], override=None, now=NOW)
    assert (r.source, r.expires_at) == ("plan", None)
    lapsed = addon(True, ends=NOW - DAY)
    r = resolve(ANALYTICS, bundles=[plan(False), lapsed], override=None, now=NOW)
    assert r.available is False


def test_limits_add_up_and_unlimited_absorbs():
    seats = addon(2, "extra_seats")
    r = resolve(STAFF, bundles=[plan(3), seats], override=None, now=NOW)
    assert (r.value, r.expires_at) == (5, seats.expires_at)
    r = resolve(STAFF, bundles=[plan(UNLIMITED, "pro"), seats], override=None, now=NOW)
    assert r.value == UNLIMITED
    r = resolve(STAFF, bundles=[plan(0)], override=None, now=NOW)
    assert (r.available, r.reason) == (False, "not_in_plan")


def test_override_is_absolute_not_additive():
    r = resolve(STAFF, bundles=[plan(10, "pro")], override=override(4), now=NOW)
    assert (r.value, r.source, r.shadowed) == (4, "override", (plan(10, "pro"),))


def test_default_when_nothing_grants():
    r = resolve(STAFF, bundles=[], override=None, now=NOW)
    assert (r.value, r.source, r.available) == (1, "default", True)


def test_kill_switch_gates_but_keeps_the_answer_visible():
    killed = Feature("advanced_analytics", "boolean", False, enabled=False)
    r = resolve(killed, bundles=[plan(True, "pro")], override=None, now=NOW)
    assert (r.available, r.reason, r.value, r.source) == (
        False, "disabled_globally", True, "plan",
    )


def test_values_are_type_checked():
    assert check_value("limit", UNLIMITED) == UNLIMITED
    assert check_value("limit", 0) == 0
    assert check_value("boolean", False) is False
    for kind, bad in (("limit", -1), ("limit", True), ("limit", 2.5),
                      ("boolean", 1), ("boolean", "true")):
        with pytest.raises(ValueError):
            check_value(kind, bad)


def test_snapshot_lives_until_the_next_boundary():
    grants = [
        override(True, starts=NOW + 5 * DAY, ends=NOW + 9 * DAY),
        addon(True, ends=NOW + 3 * DAY),
        addon(True, ends=NOW - DAY),
        plan(True),
    ]
    assert next_change(grants, NOW) == NOW + 3 * DAY
    assert next_change([plan(True)], NOW) is None


def test_bucket_is_stable_even_and_independent_per_flag():
    tenants = [str(uuid4()) for _ in range(20_000)]
    assert all(bucket("checkout_v2", t) == bucket("checkout_v2", t) for t in tenants[:50])
    in_a = {t for t in tenants if bucket("checkout_v2", t) < 2_000}
    in_b = {t for t in tenants if bucket("analytics_v2", t) < 2_000}
    assert abs(len(in_a) / len(tenants) - 0.20) < 0.015
    # Independent flags overlap like independent coins (~4%), not ~20%.
    assert abs(len(in_a & in_b) / len(tenants) - 0.04) < 0.01


def test_raising_the_percentage_only_adds_tenants():
    tenants = [str(uuid4()) for _ in range(2_000)]
    on = lambda pct: {  # noqa: E731
        t for t in tenants
        if flag_on(Flag("checkout_v2", True, pct), t, None, NOW)[0]
    }
    assert on(10) <= on(25) <= on(60) <= on(100) == set(tenants)
    assert on(0) == set()


def test_flag_order():
    t = str(uuid4())
    beta = Flag("multi_warehouse_v1", True, 0)
    assert flag_on(None, t, None, NOW) == (False, "unknown_flag")
    assert flag_on(beta, t, None, NOW) == (False, "not_in_rollout")
    assert flag_on(beta, t, FlagTarget(True), NOW) == (True, "targeted")
    assert flag_on(beta, t, FlagTarget(True, NOW - DAY), NOW) == (False, "not_in_rollout")
    # The master switch beats a target: it is the kill switch.
    off = Flag("multi_warehouse_v1", False, 100)
    assert flag_on(off, t, FlagTarget(True), NOW) == (False, "flag_off")
    # A target can hold one tenant back from a full rollout.
    everyone = Flag("checkout_v2", True, 100)
    assert flag_on(everyone, t, FlagTarget(False), NOW) == (False, "targeted")
    # Platform-level checks have no tenant: only "everyone" counts.
    assert flag_on(Flag("x", True, 50), None, None, NOW) == (False, "not_in_rollout")
    assert flag_on(everyone, None, None, NOW) == (True, "everyone")
```

---

## 16. Race conditions

| Race | Handling |
|---|---|
| A stale refill after invalidation | Versioned stamps. The stamp is read before the data, and the tenant version is bumped in the same transaction (§6.2) |
| A catalog change seen before its commit | The token is rotated only after the commit |
| Two concurrent creates both squeezing under a count limit | `pg_advisory_xact_lock(hashtextextended('quota:{tenant}:{feature}'))` is held until commit. Tested: the third product is refused |
| Two concurrent consumes at the limit | One conditional upsert statement; the row lock serializes them. Tested: 60 against 50 gives 50 |
| Consume, then the business write fails | The counter lives in the same transaction, so a rollback takes it back out. Tested |
| Two admins creating an override at once | `SELECT … FOR UPDATE` on the live row, then the partial unique index. The loser gets 409 "reload" |
| The plan changes mid-request | The request keeps the snapshot it started with (memoised), so it stays self-consistent. The next request sees the new plan |
| An expiry at the boundary | The snapshot's `until` is capped at the boundary, and the resolver compares times itself |
| Celery retries (at least once) | Consume in the same transaction as the durable record. Sends are idempotent by message key |
| A job scheduled while entitled and run after a lapse | Check again at execution time (§8.3 example 6) |
| The hub's view goes stale | UX only. A 402 or 403 invalidates the query; the backend has the final say |
| Clock skew | Every comparison uses timezone-aware UTC from the API hosts, which run NTP. The database stores `timestamptz` |

---

## 17. Security

- **Enforce on the backend, in the shared use case,** so every entry point is covered: hub, CSV, agent, API, Celery. The frontend is UX only.
- **Take the tenant from server context:** the ownership-checked path (`verify_store_ownership`) or the middleware. Never take a tenant ID or a plan from the request body.
- **Admin writes:**
  - require `require_admin`
  - require the 2FA step-up for the kill switch, plan grants and turning a flag off
  - require a reason for every write
  - write the audit row in the same transaction
- **Limit override lifetimes.** An override created in the admin must expire within 366 days unless its `source` is `contract`. Permanent exceptions have to be named contracts. `migration` overrides are written only by migrations: grandfathering and the moved API grants.
- **Close the backdoors:**
  - Make the new models read-only in SQLAdmin.
  - Make `TenantAdmin.plan` and `feature_flags` read-only there too; they can be edited today with no audit row (B6).
  - Validate plan writes against the `PLAN_LIMITS` keys.
- **Supabase.** The migration revokes `anon`/`authenticated` on the new tables. **Check B13 for the existing wallet and billing tables** (Supabase dashboard → Data API; or `SELECT grantee, privilege_type FROM information_schema.role_table_grants WHERE table_name = 'merchant_wallets'`).
- **Don't leak:**
  - The merchant endpoint leaves out `source_id` and lists only the flags that are on.
  - The storefront payload carries only derived booleans; fix B12.
  - Error details name features and plans, never other tenants.
- **Fail closed on programming errors.** An unknown feature key is unavailable and logs an error. An unknown plan gets the defaults and fires an `alert`.
- **Validate values** against the feature's kind at the admin boundary (`check_value`). The CHECK constraints are the second wall.
- **RBAC stays separate.** An entitlement never implies a staff permission, and a permission never implies an entitlement.

---

## 18. Performance

| Path | Cost |
|---|---|
| Hot read (any number of checks in one request) | 1 Redis `MGET` (~0.3 ms), 0 DB queries. The tenant row is already loaded by the middleware |
| Miss (after a change, a boundary, or ≤ 5 min) | ~6 indexed queries (~5 ms). Snapshot is ~2–4 KB of JSON |
| Global change (kill switch, plan grant) | Lazy: each active tenant recomputes once, on its next request. At 1,000 active tenants that is ~5 s of DB time spread over the following minutes |
| `check_quota` | 1 advisory lock + 1 COUNT bounded by the limit (unlimited tenants skip it) |
| `consume` | 1 upsert on a per-tenant row. Hot-row contention starts at around dozens of consumes per second *per tenant* (V3: sharded or Redis counters) |
| `usage()` | 1 COUNT or 1 PK lookup per metered feature. Billing page only |

- **Never resolve in global middleware.** Storefront requests pay nothing.
- **Quick win (B10), independent of this project:**
  ```sql
  CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_message_logs_store_template_created
      ON public.message_logs (store_id, created_at) WHERE template_name IS NOT NULL;
  ```
  WhatsApp's allowance count becomes a bounded index range scan with no code change.
- **V2:** batch resolution for admin lists and analytics, instead of N snapshots.

---

## 19. Folder and module structure

**NUMU-api:**
```
src/core/entitlements.py                                   pure rules (§5)
src/core/exceptions/base.py                                + 3 errors; PlanLimitExceededError extended
src/infrastructure/database/models/public/entitlements.py  6 models (§4.2)
src/infrastructure/database/models/public/tenant.py        + entitlements_version
src/application/services/entitlement_service.py            cache, require, quotas, explain (§20.1)
src/application/services/app_billing.py                    coverage_end() extracted (§20.2)
src/api/dependencies/entitlements.py                       get_entitlements, store_tenant, require_* (§8.1)
src/api/middleware/error_handler.py                        + 3 handlers (§8.2)
src/api/v1/routes/stores/entitlements.py                   GET entitlements, GET usage
src/api/v1/routes/admin/entitlements.py                    features, grants, overrides, flags, explain, audit
alembic/versions/2026MMDD_entitlements.py                  DDL + seed + grandfather (§20.4)
tests/unit/test_entitlements.py                            §15
tests/unit/test_entitlements_seed.py                       seed parity
tests/integration/test_entitlement_service.py              the 8 scenarios
```
- **Deleted in Phase 3:** `plan_limit_service.py`, `api/dependencies/plan.py`, `api/dependencies/feature_flags.py`, capability plan ranks, and the feature half of `admin/plan_limits.py`.
- **`main.py`'s plan-limit refresh loop stays** only while prices are hot-patched. V2 moves prices to a table, and the loop goes with it.

**numo-merchant-hub:**
```
src/services/entitlementsApi.ts
src/hooks/useEntitlements.ts
src/components/FeatureGate.tsx
src/components/billing/UpgradeDialog.tsx   (+ useUpgradeDialog zustand store)
src/services/api.ts, src/lib/api-error.ts  (403 body + code fix)
```

**numu-admin:**
```
client/src/services/entitlementsAdminApi.ts
client/src/pages/Features.tsx, FeatureDetail.tsx, Flags.tsx
client/src/components/merchants/EntitlementsPanel.tsx   (generalised ApiAccessPanel)
client/src/lib/adminNav.ts, client/src/App.tsx          (+2 nav items, routes)
```

---

## 20. Production code

The pure core is in §5, the models in §4.2, the dependencies and handlers in §8, and the tests in §15.

### 20.1 `src/application/services/entitlement_service.py`

This file was run end to end against the real NUMU models, Postgres 15 and Redis (Appendix A).

```python
"""The one place that answers "may this tenant use X, and how much of it".

Callers ask ``require`` / ``has`` / ``limit`` / ``flag`` / ``check_quota`` /
``consume`` and never learn where a grant came from. Admin tools ask
``explain``.

A read costs one Redis round trip. The tenant's resolved snapshot is stamped
with ``[tenants.entitlements_version, catalog token, tenants.plan]``; a stamp
that no longer matches is recomputed on the spot. Writers bump a version
instead of deleting a key, so there is no delete-then-stale-refill race, and
a plan change needs no invalidation code at all: the plan is in the stamp.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.app_billing import coverage_end
from src.core.entities.plan import PLAN_LIMITS
from src.core.entitlements import (
    UNLIMITED,
    Feature,
    Flag,
    FlagTarget,
    Grant,
    Resolved,
    flag_on,
    next_change,
    resolve,
)
from src.core.exceptions import (
    FeatureDisabledError,
    FeatureNotAvailableError,
    PlanLimitExceededError,
)
from src.core.logging import get_logger
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.public.app import AppModel
from src.infrastructure.database.models.public.app_billing import (
    AppSubscriptionModel,
)
from src.infrastructure.database.models.public.entitlements import (
    EntitlementOverrideModel,
    FeatureFlagModel,
    FeatureFlagTargetModel,
    FeatureModel,
    PlanEntitlementModel,
    UsageCounterModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.product import ProductModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)

CATALOG_KEY = "ent:catalog"
#: The safety net for a missed bump. Invalidation is the stamp, not this.
SNAPSHOT_TTL = timedelta(minutes=5)
LIFETIME = datetime(1970, 1, 1, tzinfo=UTC)
#: Fields the merchant hub gets; source_id stays server-side.
PUBLIC_FIELDS = (
    "value",
    "available",
    "source",
    "expires_at",
    "reason",
    "kind",
    "period",
)

Counter = Callable[[AsyncSession, TenantModel, datetime | None], Awaitable[int]]


def _tenant_stores(tenant: TenantModel):
    return select(StoreModel.id).where(StoreModel.tenant_id == tenant.id)


async def _count_products(db, tenant, since):
    return await db.scalar(
        select(func.count())
        .select_from(ProductModel)
        .where(ProductModel.store_id.in_(_tenant_stores(tenant)))
    )


async def _count_orders(db, tenant, since):
    # Bounded by ix_orders_store_created_status: at most `limit` index entries
    # are ever walked for a tenant that has a limit at all.
    return await db.scalar(
        select(func.count())
        .select_from(OrderModel)
        .where(
            OrderModel.store_id.in_(_tenant_stores(tenant)),
            OrderModel.created_at >= since,
        )
    )


#: usage = "count" features: the real rows are the meter. Keep this list in
#: step with the catalog; a missing entry fails loudly in check_quota.
COUNTERS: dict[str, Counter] = {
    "products": _count_products,
    "orders_per_month": _count_orders,
}


def period_bounds(
    period: str | None, now: datetime
) -> tuple[datetime, datetime | None]:
    """UTC calendar buckets. ``(1970-01-01, None)`` for lifetime meters."""
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, (start + timedelta(days=32)).replace(day=1)
    return LIFETIME, None


@dataclass(frozen=True)
class Inputs:
    """Everything one tenant's answers are computed from."""

    features: dict[str, FeatureModel]
    grants: dict[str, list[Grant]]
    overrides: dict[str, EntitlementOverrideModel]
    flags: list[FeatureFlagModel]
    targets: dict[str, FlagTarget]


def _feature(row: FeatureModel) -> Feature:
    return Feature(row.key, row.kind, row.default_value, row.is_enabled)


def _override(row: EntitlementOverrideModel) -> Grant:
    return Grant("override", str(row.id), row.value, row.starts_at, row.expires_at)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


class EntitlementService:
    def __init__(self, db: AsyncSession, cache: RedisCacheService | None = None):
        self.db = db
        self.cache = cache or RedisCacheService()
        self._memo: dict[UUID, dict[str, Any]] = {}

    # ─── Reads ────────────────────────────────────────────────────────────

    async def snapshot(self, tenant: TenantModel) -> dict[str, Any]:
        """The tenant's resolved features and flags. One Redis call, memoised
        for the life of this service (one request)."""
        if (snap := self._memo.get(tenant.id)) is not None:
            return snap
        key = f"ent:{tenant.id}"
        cached = await self.cache.get_many([CATALOG_KEY, key])
        # Stamp before computing: data read after the version is at least as
        # new as the version, so a stale snapshot can never carry a fresh stamp.
        stamp = [tenant.entitlements_version, cached.get(CATALOG_KEY), tenant.plan]
        now = datetime.now(UTC)
        snap = cached.get(key)
        if not (
            snap
            and snap["stamp"] == stamp
            and datetime.fromisoformat(snap["until"]) > now
        ):
            snap = await self._compute(tenant, stamp, now)
            ttl = datetime.fromisoformat(snap["until"]) - now
            await self.cache.set(key, snap, expire=max(1, int(ttl.total_seconds())))
        self._memo[tenant.id] = snap
        return snap

    async def feature(self, tenant: TenantModel, key: str) -> dict[str, Any]:
        state = (await self.snapshot(tenant))["features"].get(key)
        if state is None:
            # Code asked for a key the catalog lacks: fail closed, loudly.
            logger.error("entitlements_unknown_feature", feature=key)
            return {"value": False, "available": False, "reason": "unknown_feature"}
        return state

    async def has(self, tenant: TenantModel, key: str) -> bool:
        return bool((await self.feature(tenant, key))["available"])

    async def limit(self, tenant: TenantModel, key: str) -> int | str:
        """The entitled amount: an int, or UNLIMITED. The kill switch is
        enforced by require/check_quota/consume, not here."""
        return (await self.feature(tenant, key))["value"]

    async def flag(self, tenant: TenantModel, key: str) -> bool:
        return key in (await self.snapshot(tenant))["flags"]

    async def require(self, tenant: TenantModel, key: str) -> dict[str, Any]:
        state = await self.feature(tenant, key)
        if state["available"]:
            return state
        if state["reason"] == "disabled_globally":
            raise FeatureDisabledError(key)
        via = await self._available_via(key) if state["reason"] == "not_in_plan" else []
        raise FeatureNotAvailableError(key, reason=state["reason"], available_via=via)

    # ─── Limits ───────────────────────────────────────────────────────────

    async def check_quota(self, tenant: TenantModel, key: str, adding: int = 1) -> None:
        """Hard limit for resources counted from real rows (products, staff).
        Serialises per tenant+feature until the caller's transaction ends, so
        two concurrent creates cannot both squeeze under the limit."""
        state = await self.require(tenant, key)
        limit = state["value"]
        if limit == UNLIMITED:
            return
        dialect = getattr(getattr(self.db, "bind", None), "dialect", None)
        if dialect is not None and dialect.name == "postgresql":
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": f"quota:{tenant.id}:{key}"},
            )
        start, resets = period_bounds(state["period"], datetime.now(UTC))
        used = await COUNTERS[key](self.db, tenant, start) or 0
        if used + adding > limit:
            raise await self._limit_error(tenant, key, limit, used, resets)

    async def consume(self, tenant: TenantModel, key: str, amount: int = 1) -> int:
        """Add to a metered counter (orders, messages, runs) in the caller's
        transaction. Hard limits refuse atomically; a rollback un-counts."""
        state = await self.require(tenant, key)
        limit = state["value"]
        hard = state["enforcement"] == "hard" and limit != UNLIMITED
        start, resets = period_bounds(state["period"], datetime.now(UTC))
        if hard and amount > limit:
            raise await self._limit_error(tenant, key, limit, 0, resets)
        insert = pg_insert(UsageCounterModel).values(
            tenant_id=tenant.id, feature_key=key, period_start=start, used=amount
        )
        used = await self.db.scalar(
            insert.on_conflict_do_update(
                index_elements=["tenant_id", "feature_key", "period_start"],
                set_={
                    "used": UsageCounterModel.used + insert.excluded.used,
                    "updated_at": func.now(),
                },
                where=(UsageCounterModel.used + insert.excluded.used <= limit)
                if hard
                else None,
            ).returning(UsageCounterModel.used)
        )
        if used is None:
            current = await self._counter(tenant, key, start)
            raise await self._limit_error(tenant, key, limit, current, resets)
        if limit != UNLIMITED and used > limit:
            # Soft limit crossed: V2 hands this to billing (overage) and to
            # the merchant notification centre, once per period.
            logger.insight("usage_over_soft_limit", feature=key, used=used, limit=limit)
        return used

    async def usage(self, tenant: TenantModel, key: str) -> dict[str, Any]:
        state = await self.feature(tenant, key)
        start, resets = period_bounds(state.get("period"), datetime.now(UTC))
        if state.get("usage") == "counter":
            used = await self._counter(tenant, key, start)
        else:
            used = await COUNTERS[key](self.db, tenant, start) or 0
        limit = state["value"]
        return {
            "feature": key,
            "limit": limit,
            "used": used,
            "remaining": UNLIMITED if limit == UNLIMITED else max(0, limit - used),
            "resets_at": _iso(resets),
        }

    # ─── Explain (admin; never cached) ────────────────────────────────────

    async def explain(self, tenant: TenantModel, key: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        inputs = await self._inputs(tenant, now)
        row = inputs.features[key]
        override = inputs.overrides.get(key)
        grants = inputs.grants.get(key, [])
        result = resolve(
            _feature(row),
            bundles=grants,
            override=_override(override) if override else None,
            now=now,
        )
        cached = (await self.cache.get(f"ent:{tenant.id}") or {}).get("features", {})
        return {
            **self._public(result, row),
            "source_id": result.source_id,
            "layers": [
                {
                    "layer": "kill_switch",
                    "enabled": row.is_enabled,
                    "reason": row.disabled_reason,
                },
                {
                    "layer": "override",
                    "row": override
                    and {
                        "id": str(override.id),
                        "value": override.value,
                        "source": override.source,
                        "reason": override.reason,
                        "created_by": override.created_by and str(override.created_by),
                        "starts_at": _iso(override.starts_at),
                        "expires_at": _iso(override.expires_at),
                        "live": result.source == "override",
                    },
                },
                *(
                    {
                        "layer": g.source,
                        "id": g.source_id,
                        "value": g.value,
                        "expires_at": _iso(g.expires_at),
                        "live": g.expires_at is None or g.expires_at > now,
                        "shadowed": g in result.shadowed,
                    }
                    for g in grants
                ),
                {"layer": "default", "value": row.default_value},
            ],
            "cache_agrees": cached.get(key, {}).get("value") == result.value,
        }

    # ─── Writers call these ───────────────────────────────────────────────

    @staticmethod
    async def bump_tenant(db: AsyncSession, tenant_id: UUID) -> None:
        """Inside the writer's transaction, next to the change it announces."""
        await db.execute(
            update(TenantModel)
            .where(TenantModel.id == tenant_id)
            .values(entitlements_version=TenantModel.entitlements_version + 1)
        )

    @staticmethod
    async def bump_catalog(cache: RedisCacheService | None = None) -> None:
        """AFTER the commit of a catalog, plan-grant or flag change. A random
        token, not a counter, so a Redis restart can never reissue an old one."""
        if not await (cache or RedisCacheService()).set(CATALOG_KEY, uuid4().hex):
            logger.alert("entitlements_catalog_bump_failed")

    # ─── Internals ────────────────────────────────────────────────────────

    async def _inputs(self, tenant: TenantModel, now: datetime) -> Inputs:
        bundles = {tenant.plan: Grant("plan", tenant.plan, False)}
        subs = await self.db.execute(
            select(AppModel.slug, AppSubscriptionModel)
            .join(AppModel, AppModel.id == AppSubscriptionModel.app_id)
            .where(AppSubscriptionModel.tenant_id == tenant.id)
        )
        for slug, sub in subs.all():
            # Lapsed ones too: resolve() skips them, explain() shows them.
            if (end := coverage_end(sub)) is not None:
                bundles[f"addon:{slug}"] = Grant("addon", slug, False, expires_at=end)

        grants: dict[str, list[Grant]] = {}
        rows = await self.db.scalars(
            select(PlanEntitlementModel).where(
                PlanEntitlementModel.plan_key.in_(list(bundles))
            )
        )
        seen_plan = False
        for row in rows:
            base = bundles[row.plan_key]
            seen_plan |= base.source == "plan"
            grants.setdefault(row.feature_key, []).append(
                Grant(
                    base.source, base.source_id, row.value, expires_at=base.expires_at
                )
            )
        for listed in grants.values():
            listed.sort(key=lambda g: g.source != "plan")  # plan first, then add-ons
        if not seen_plan:
            logger.alert("entitlements_unknown_plan", plan=tenant.plan)

        return Inputs(
            features={f.key: f for f in await self.db.scalars(select(FeatureModel))},
            grants=grants,
            overrides={
                o.feature_key: o
                for o in await self.db.scalars(
                    select(EntitlementOverrideModel).where(
                        EntitlementOverrideModel.tenant_id == tenant.id,
                        EntitlementOverrideModel.revoked_at.is_(None),
                    )
                )
            },
            flags=list(await self.db.scalars(select(FeatureFlagModel))),
            targets={
                t.flag_key: FlagTarget(t.enabled, t.expires_at)
                for t in await self.db.scalars(
                    select(FeatureFlagTargetModel).where(
                        FeatureFlagTargetModel.tenant_id == tenant.id
                    )
                )
            },
        )

    async def _compute(
        self, tenant: TenantModel, stamp: list, now: datetime
    ) -> dict[str, Any]:
        inputs = await self._inputs(tenant, now)
        overrides = {k: _override(o) for k, o in inputs.overrides.items()}
        features = {
            key: self._public(
                resolve(
                    _feature(row),
                    bundles=inputs.grants.get(key, []),
                    override=overrides.get(key),
                    now=now,
                ),
                row,
            )
            for key, row in inputs.features.items()
        }
        flags = sorted(
            f.key
            for f in inputs.flags
            if flag_on(
                Flag(f.key, f.enabled, f.rollout_percent),
                str(tenant.id),
                inputs.targets.get(f.key),
                now,
            )[0]
        )
        boundaries = [g for gs in inputs.grants.values() for g in gs]
        boundaries += overrides.values()
        boundaries += [
            Grant("flag", k, True, expires_at=t.expires_at)
            for k, t in inputs.targets.items()
        ]
        nxt = next_change(boundaries, now)
        until = min(nxt, now + SNAPSHOT_TTL) if nxt else now + SNAPSHOT_TTL
        return {
            "stamp": stamp,
            "until": until.isoformat(),
            "features": features,
            "flags": flags,
        }

    @staticmethod
    def _public(result: Resolved, row: FeatureModel) -> dict[str, Any]:
        return {
            "value": result.value,
            "available": result.available,
            "source": result.source,
            "source_id": result.source_id,
            "expires_at": _iso(result.expires_at),
            "reason": result.reason,
            "kind": row.kind,
            "usage": row.usage,
            "period": row.period,
            "enforcement": row.enforcement,
        }

    async def _counter(self, tenant: TenantModel, key: str, start: datetime) -> int:
        return (
            await self.db.scalar(
                select(UsageCounterModel.used).where(
                    UsageCounterModel.tenant_id == tenant.id,
                    UsageCounterModel.feature_key == key,
                    UsageCounterModel.period_start == start,
                )
            )
            or 0
        )

    async def _available_via(self, key: str) -> list[str]:
        """Sellable plans and add-ons that would turn this on (upsell copy)."""
        rows = await self.db.execute(
            select(PlanEntitlementModel.plan_key, PlanEntitlementModel.value).where(
                PlanEntitlementModel.feature_key == key
            )
        )
        sellable = {k for k, p in PLAN_LIMITS.items() if p.monthly_price_piasters}
        return sorted(
            plan
            for plan, value in rows.all()
            if value not in (False, 0)
            and (plan in sellable or plan.startswith("addon:"))
        )

    async def _limit_error(
        self, tenant, key, limit, used, resets
    ) -> PlanLimitExceededError:
        return PlanLimitExceededError(
            resource=key,
            limit=limit,
            current=used,
            plan=tenant.plan,
            feature=key,
            resets_at=_iso(resets),
            available_via=await self._available_via(key),
        )
```

### 20.2 Exceptions and `coverage_end`

These go in `src/core/exceptions/base.py`, next to the existing `PlanLimitExceededError`, and must also be exported from `src/core/exceptions/__init__.py`. They were run in the end-to-end test.

```python
class FeatureNotAvailableError(DomainException):
    """Not entitled: the plan lacks it, an add-on lapsed, or an override blocks it."""

    def __init__(
        self, feature: str, *, reason: str | None, available_via: list[str]
    ) -> None:
        self.feature = feature
        self.reason = reason
        self.available_via = available_via
        self.upgrade_required = bool(available_via)
        super().__init__(
            f"Your plan does not include {feature}.", code="FEATURE_NOT_AVAILABLE"
        )


class FeatureDisabledError(DomainException):
    """Switched off platform-wide by the kill switch. Not the merchant's doing."""

    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(
            f"{feature} is temporarily unavailable.",
            code="FEATURE_TEMPORARILY_DISABLED",
        )


class FeatureNotReleasedError(DomainException):
    """The release flag is off for this tenant: behave as if the route is absent."""

    def __init__(self, flag: str) -> None:
        self.flag = flag
        super().__init__("Not found", code="FEATURE_NOT_RELEASED")


class PlanLimitExceededError(DomainException):
    """Raised when an action would exceed the tenant's plan limits."""

    def __init__(
        self,
        resource: str,
        limit: int,
        current: int,
        plan: str,
        upgrade_to: str | None = None,
        *,
        feature: str | None = None,
        resets_at: str | None = None,
        available_via: list[str] | tuple[str, ...] = (),
    ) -> None:
        self.resource = resource
        self.limit = limit
        self.current = current
        self.plan = plan
        self.feature = feature or resource
        self.resets_at = resets_at
        self.available_via = list(available_via)
        self.upgrade_to = upgrade_to or next(iter(self.available_via), None)
        message = (
            f"Plan limit reached: your {plan} plan allows {limit} {resource} "
            f"(currently at {current}). Upgrade to continue."
        )
        super().__init__(message, code="PLAN_LIMIT_EXCEEDED")
```

In `app_billing.py`, extract the coverage rule so that `covers()` and the resolver can never disagree:

```python
def coverage_end(sub: AppSubscriptionModel | None) -> datetime | None:
    """When the store stops being covered: the paid period's end plus the
    3-day grace, or the bare period end once the merchant cancelled. None
    when nothing is live."""
    if sub is None or sub.status not in ("active", "past_due"):
        return None
    end = _aware(sub.current_period_end)
    if not (sub.status == "active" and sub.cancel_at_period_end):
        end += GRACE
    return end


def covers(sub: AppSubscriptionModel | None, now: datetime) -> bool:
    """Whether the store may use the app at ``now``: a paid period, plus the
    3-day grace after a lapse. A merchant who cancelled gets no grace."""
    end = coverage_end(sub)
    return end is not None and end > now
```

### 20.3 Admin: create an override

This is the most involved write. It covers validation, superseding the old override, the bump, the audit row and the 409. The other admin writes follow the same shape: validate, write, `bump_tenant` or (commit + `bump_catalog`), and audit in the same transaction.

```python
_STEP_UP = [Depends(require_admin_2fa(max_age_seconds=300))]
#: Only a named contract may run longer than a year, or forever.
MAX_OVERRIDE = timedelta(days=366)


class OverrideIn(BaseModel):
    feature_key: str
    value: bool | int | Literal["unlimited"]
    source: Literal["support", "sales", "promotion", "beta", "contract", "testing"]
    reason: str = Field(min_length=3, max_length=500)
    starts_at: datetime | None = None
    expires_at: datetime | None = None


@router.post("/tenants/{tenant_id}/overrides", status_code=201)
async def create_override(
    tenant_id: UUID,
    body: OverrideIn,
    admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[dict]:
    feature = await db.get(FeatureModel, body.feature_key)
    if feature is None or await db.get(TenantModel, tenant_id) is None:
        raise HTTPException(status_code=404, detail="Unknown feature or tenant")
    try:
        value = check_value(feature.kind, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    now = datetime.now(UTC)
    starts = body.starts_at or now
    if body.expires_at is not None and body.expires_at <= starts:
        raise HTTPException(status_code=422, detail="expires_at must be after starts_at")
    if body.source != "contract" and (
        body.expires_at is None or body.expires_at - starts > MAX_OVERRIDE
    ):
        raise HTTPException(
            status_code=422, detail="Only a contract override may exceed one year"
        )

    previous = await db.scalar(
        select(EntitlementOverrideModel)
        .where(
            EntitlementOverrideModel.tenant_id == tenant_id,
            EntitlementOverrideModel.feature_key == feature.key,
            EntitlementOverrideModel.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if previous is not None:
        previous.revoked_at, previous.revoked_by = now, admin_id
    row = EntitlementOverrideModel(
        tenant_id=tenant_id,
        feature_key=feature.key,
        value=value,
        starts_at=starts,
        expires_at=body.expires_at,
        source=body.source,
        reason=body.reason.strip(),
        created_by=admin_id,
    )
    db.add(row)
    await EntitlementService.bump_tenant(db, tenant_id)
    await AuditService(db).log(
        event_type="entitlement.override.create",
        action="create",
        resource_type="feature",
        resource_id=feature.key,
        tenant_id=tenant_id,
        user_id=admin_id,
        old_value=previous
        and {"id": str(previous.id), "value": previous.value,
             "expires_at": previous.expires_at and previous.expires_at.isoformat()},
        new_value={"value": value, "source": body.source,
                   "starts_at": starts.isoformat(),
                   "expires_at": body.expires_at and body.expires_at.isoformat()},
        details={"reason": body.reason},
    )
    try:
        await db.flush()
    except IntegrityError as exc:  # uq_entitlement_overrides_live: a concurrent create
        raise HTTPException(
            status_code=409, detail="This override changed a moment ago. Reload."
        ) from exc
    return SuccessResponse(data={"id": str(row.id)})
```

The kill switch follows the same shape with `dependencies=_STEP_UP`:
1. Lock the feature row.
2. Flip `is_enabled` and set `disabled_reason`.
3. Write the audit row with severity `warning`.
4. `await db.commit()`, and only then `await EntitlementService.bump_catalog()`.

### 20.4 Migration

The revision id must be at most 32 characters. Point `down_revision` at the head that `alembic heads` prints, or merge the heads first. The DDL is §4.1 exactly. The seed below was checked against the live `PLAN_LIMITS`.

```python
"""Entitlements, release flags and usage counters.

Revision ID: entitlements_20260925
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "entitlements_20260925"
down_revision = "<alembic heads>"
branch_labels = None
depends_on = None

DDL = """ … §4.1 verbatim … """

U = "unlimited"

# key, name, name_ar, category, kind, default, usage, period, enforcement
FEATURES = [
    ("products", "Products", "المنتجات", "catalog", "limit", 100, "count", None, "hard"),
    ("orders_per_month", "Orders per month", "الطلبات شهريًا", "orders", "limit", U,
     "count", "month", "soft"),
    ("stores", "Stores", "المتاجر", "account", "limit", 1, "count", None, "hard"),
    ("staff_accounts", "Staff accounts", "حسابات الموظفين", "account", "limit", 3,
     "count", None, "hard"),
    ("partner_apps", "Partner apps", "تطبيقات الشركاء", "apps", "limit", U, "count",
     None, "hard"),
    ("api_access", "API & webhooks", "الواجهة البرمجية", "developers", "boolean", False,
     None, None, "hard"),
    ("custom_domain", "Custom domain", "نطاق مخصص", "online_store", "boolean", False,
     None, None, "hard"),
    ("discount_codes", "Discount codes", "أكواد الخصم", "marketing", "boolean", False,
     None, None, "hard"),
    ("multi_warehouse", "Multiple locations", "فروع متعددة", "logistics", "boolean",
     False, None, None, "hard"),
    ("product_subscriptions", "Product subscriptions", "اشتراكات المنتجات", "catalog",
     "boolean", False, None, None, "hard"),
]

# A literal snapshot of PLAN_LIMITS on 2026-09-24, not an import: a migration
# that imports app code breaks the day that code changes.
FIELD_TO_FEATURE = {
    "max_products": "products",
    "max_orders_per_month": "orders_per_month",
    "max_stores": "stores",
    "max_staff_members": "staff_accounts",
    "api_access_enabled": "api_access",
    "custom_domain_enabled": "custom_domain",
    "discount_codes_enabled": "discount_codes",
}
# plan: products, orders/mo, stores, staff, api, custom_domain, discount_codes
PLANS = {
    "demo": (10, 50, 1, 1, False, False, False),
    "trial": (-1, 500, 1, 3, False, True, True),
    "starter": (100, -1, 1, 3, False, True, True),
    "pro": (-1, -1, 3, 10, True, True, True),
    "developer": (-1, -1, 0, 3, True, False, True),
    "enterprise": (-1, -1, -1, -1, True, True, True),
    "payg": (100, -1, 1, 3, False, True, True),
    "free": (50, 100, 1, 1, False, False, False),
}
# Written by public/beta.py but never defined: today it silently gets trial.
PLANS["beta"] = PLANS["trial"]
PRO_AND_UP = {"pro", "developer", "enterprise"}  # capability min_plan="pro"


def seed_rows(overrides: dict) -> list[tuple[str, str, object]]:
    """Plan grants, with prod's /admin/plan-limits edits layered on top."""
    rows = []
    for plan, values in PLANS.items():
        fields = dict(zip(FIELD_TO_FEATURE, values, strict=True))
        fields.update(
            (k, v) for k, v in (overrides.get(plan) or {}).items() if k in FIELD_TO_FEATURE
        )
        for field, feature in FIELD_TO_FEATURE.items():
            rows.append((plan, feature, U if fields[field] == -1 else fields[field]))
        rows.append((plan, "partner_apps", U))
        for feature in ("multi_warehouse", "product_subscriptions"):
            rows.append((plan, feature, plan in PRO_AND_UP))
    return rows


def upgrade() -> None:
    op.execute(DDL)
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO public.features (key, name, name_ar, category, kind,"
            " default_value, usage, period, enforcement) VALUES (:key, :name,"
            " :name_ar, :category, :kind, CAST(:default AS jsonb), :usage, :period,"
            " :enforcement) ON CONFLICT (key) DO NOTHING"
        ),
        [
            dict(zip(("key", "name", "name_ar", "category", "kind", "default",
                      "usage", "period", "enforcement"), f, strict=True))
            | {"default": json.dumps(f[5])}
            for f in FEATURES
        ],
    )
    overrides = conn.execute(
        sa.text("SELECT value FROM public.platform_config WHERE key = 'plan_limits'")
    ).scalar() or {}
    conn.execute(
        sa.text(
            "INSERT INTO public.plan_entitlements (plan_key, feature_key, value)"
            " VALUES (:p, :f, CAST(:v AS jsonb)) ON CONFLICT DO NOTHING"
        ),
        [{"p": p, "f": f, "v": json.dumps(v)} for p, f, v in seed_rows(overrides)],
    )
    # Per-tenant API grants move out of the flags JSON into audited overrides.
    conn.execute(
        sa.text(
            "INSERT INTO public.entitlement_overrides"
            " (tenant_id, feature_key, value, source, reason)"
            " SELECT id, 'api_access', 'true', 'migration',"
            "        'moved from tenants.feature_flags.api_access'"
            " FROM public.tenants"
            " WHERE (feature_flags ->> 'api_access')::boolean"
            " ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS public.usage_counters, public.feature_flag_targets,"
        " public.feature_flags, public.entitlement_overrides,"
        " public.plan_entitlements, public.features;"
        " ALTER TABLE public.tenants DROP COLUMN IF EXISTS entitlements_version;"
    )
```

**Why the moved API grants have no expiry.** They are `source='migration'`, which only migrations can write: the admin API's `source` field doesn't accept it. So the admin's one-year rule doesn't apply to them. An admin who later edits one of these grants replaces it with an admin-sourced override, and that one does follow the rule.

**Why `orders_per_month` is `soft` here.** Phase 1 keeps today's hub-only hard block by calling `usage()` and `limit()` at the same three routes. D2 then decides what "over the limit" means.

---

## 21. V1, V2, V3

### V1: build now

| Item | Size |
|---|---|
| Migrations: 6 tables, `entitlements_version`, seed, API-grant move, Supabase revoke; plus the concurrent audit index | S |
| `src/core/entitlements.py` and its tests (written here) | S |
| `EntitlementService`, dependencies, 3 error codes plus the extended 402 (written here) | M |
| Merchant `GET /entitlements` and `/entitlements/usage`; rebuild `/plan/limits` from the catalog | S |
| Hub: the 403-body and `ApiError.code` fix, `useEntitlements`, `FeatureGate`, `UpgradeDialog`, invalidation after billing | M |
| Admin: Features list and detail (grants, overrides, kill switch, history), Flags (switch, %, targets, evaluate), Merchant Entitlements panel with Explain | M–L |
| Migration phases 0–1 (shadow week, internals swapped) and Phase 3 deletions | M |
| Independent quick wins: the `message_logs` partial index (B10), checking Supabase grants (B13), read-only SQLAdmin for plan and flags (B6), dropping `tenant_feature_flags` from the public payload (B12) | S |

**Not in V1:**
- segments and rules
- plan versioning
- overage billing
- usage events
- pushing updates to the hub
- migrating WhatsApp
- storefront entitlements
- moving prices out of `PLAN_LIMITS`

`usage_counters` ships with the table in V1, but it has no caller until the first counter-metered feature lands (automation runs or WhatsApp). The WhatsApp partial index covers today's need.

### V2: when NUMU has a few thousand active merchants, or its first usage-priced product

- **Usage billing:**
  - wallet-debited overage for soft meters
  - an append-only `usage_events` table behind the counters, for billing disputes
  - one notification per period when a limit is crossed (`emit_notification`)
- **WhatsApp:** move it onto NUMU Apps pricing, an `addon:whatsapp` grant and a counter meter with **billing-anchored periods**. Retire the billing half of the access state machine.
- **Targeting as typed columns, not a rules engine:**
  - `feature_flags.plans text[]`
  - `countries text[]` (NUMU already has `stores.country` and the market registry)
  - `created_after timestamptz`
  - `internal_only boolean` (`tenants.is_internal`)
  - Each is one column and one `if` in `flag_on`.
- **Plans:**
  - plan versioning (`pro_2027`)
  - grandfather tooling: bulk overrides under a campaign tag
  - scheduled grant changes ("from 1 Jan, Pro includes X")
- **Flag hygiene:** a stale-flag report, and a removal checklist in the PR template.
- **Admin:**
  - a global `/audit` page
  - bulk overrides with an impact preview
  - an "expiring soon" list and merchant emails (the optional nightly job, plus an `expires_at` index)
- **Hub freshness:** push invalidations over the hub's existing SSE (`useSSE.ts`), so a grant appears without a refocus.
- **Batch resolution** for admin lists and analytics ("how many Starter tenants use X?").
- **Storefront `entitlements` booleans,** with revalidation of both tags.
- **Prices move from hot-patched `PLAN_LIMITS` into a table.** That fixes B5 at the root and removes `main.py`'s refresh loop.

### V3: only at Shopify scale (100k+ merchants, many teams)

- **Entitlements as their own service,** with their own datastore, SLOs, and an SDK that evaluates in-process from a pushed configuration stream, the LaunchDarkly relay model. There is no per-request Redis call, and there are regional replicas.
- **An event-sourced usage pipeline:** Kafka → an OLAP store; rated usage; credits, commitments and contracts; an invoicing engine (Orb/Metronome/Stripe-Billing grade); reconciliation.
- **Sharded counters** for hot tenants, reconciled periodically; regional kill switches; percentage kills.
- **An experimentation platform:** A/B assignment with metrics, sequential testing, OpenFeature.
- **A rules engine and segment DSL.** Only here, with approval workflows: four eyes on kill switches, change calendars, and code references that scan for flag keys.
- **Grants from many billing systems:** app stores, marketplaces, partners, enterprise quote-to-cash, SCIM.

---

## 22. Decisions needed from you

| # | Decision | Recommendation |
|---|---|---|
| D1 | Which Starter is true: your prompt (unlimited products, 100 orders a month) or the code (100 products, unlimited orders)? | **Decided 2026-09-24:** unlimited products; orders stay unlimited. Seeded that way, and editable per plan in the admin (Features & plans, or the old Plan limits page, which now writes the catalog) |
| D2 | What does "over the order limit" mean? | **Applied 2026-09-24:** no order is refused for the monthly limit, including in the hub. The first order past it in a month writes one important `plan.orders_over_limit` notification. Overage billing from the wallet is V2 |
| D3 | Webhooks on Starter: deliver them, or fold them into `api_access`? | **Applied 2026-09-24:** creating a webhook requires `api_access`, which delivery already required. `webhooks_enabled` decides nothing any more |
| D4 | Enforce the fields that are never enforced (staff, custom domain, discount codes, multi-warehouse, product subscriptions)? | **Applying 2026-09-24, one PR per field, each with its grandfather migration.** Staff: a seat is a staff member who was not removed or a pending invitation, checked when the invitation is sent; teams already over their plan keep unlimited staff. Custom domain: connecting one needs `custom_domain`; tenants that already have one keep it |
| D5 | Abandoned-cart recovery as Pro-only? Everyone has it today | If yes, grandfather every current user |
| D6 | The `beta` plan | **Applied:** seeded as trial, which keeps today's behaviour. Map it to a real plan when the beta ends |

---

## Appendix A: verification performed

All of it ran on 2026-09-24 on the dev machine, against throwaway resources, which were dropped afterwards.

1. **The resolver: `pytest` over the 13 tests in §15. All passed.**
   - `ruff check` with the repo's `pyproject.toml` is clean for the core, the service, the models and the exceptions.
2. **The schema and the quota: Postgres 15.15 in the local `postgres_sm` container, database `ent_design_scratch`.**
   - The value CHECKs reject `-1`, `2.5`, `"lots"`, `null` and `[1]`.
   - `usage` is rejected on boolean features.
   - A second live override is rejected; a revoked one isn't counted.
   - A window where `expires_at` falls before `starts_at` is rejected.
   - **60 concurrent `consume` calls against a limit of 50: exactly 50 succeeded, and the counter reads 50.**
   - A rolled-back consume left the counter at 50.
3. **The service end to end: the real NUMU SQLAlchemy models, database `ent_design_e2e`, and Redis DB 15.** The tables were created with `create_all` over the foreign-key and relationship closure of the tables the service touches.

   | # | Scenario | Result |
   |---|---|---|
   | 1 | A miss, then a hit | 1 recompute over 2 requests |
   | 2 | An override plus `bump_tenant` | live on the next request, `source=override` |
   | 3 | `tenants.plan` changed **without** a bump | the stamp caught it |
   | 4 | Kill switch plus `bump_catalog` | `FeatureDisabledError`; the value is still visible |
   | 5 | A Pro plan's 1,000 messages plus a 500-message add-on subscription | 1,500. Explain layers in order: kill_switch, override, plan, addon, default; `cache_agrees` true |
   | 6 | `consume` 1,500, then 1 more | 402 with `limit=1500, current=1500`; `usage().remaining == 0` |
   | 7 | `check_quota` with a limit of 2 and 2 products | refused |
   | 8 | A target set to `enabled=false` on a flag at 100% | off for that tenant |

   There were 7 recomputes over the 8 scenarios, one per change, and none on hits. No unexpected alerts were logged.

   Two things the harness had to work around. Neither affects production, because migrations own the real DDL:
   - `create_all` can't build the models as they are. Plain-string `server_default`s such as `"'{}'::jsonb"` get double-quoted.
   - `immutable_text_array_to_string` is defined only in a migration.
4. **The seed: `seed_rows({})` compared with the live `PLAN_LIMITS` and `_PLAN_RANK`.** Exact match: 90 rows, 9 plans. Admin overrides layer on top correctly, and no boolean ever becomes `"unlimited"`.
