# Feature Specification: Shopify Billing-Sync Endpoint

**Feature Branch**: `backend-001-billing-sync`
**Created**: 2026-05-08
**Status**: Draft

## Why this feature exists

The Shopify-app companion repo (`numu-payments-intelligence` v002) calls
`POST /api/v1/shopify/{store_id}/billing/sync` after every Shopify
subscription create/cancel/upgrade/downgrade. The body is:

```json
{
  "subscription_id": "gid://shopify/AppSubscription/123",
  "status": "ACTIVE" | "ACCEPTED" | "PENDING" | "DECLINED" | "EXPIRED" | "CANCELLED" | "FROZEN",
  "plan_id": "starter" | "growth" | "scale",
  "is_trial": true,
  "trial_ends_at": "2026-05-22T00:00:00Z",
  "current_period_end": "2026-06-08T00:00:00Z"
}
```

This endpoint **does not exist yet** in numu-api. The Shopify-app code
catches the failure and continues (best-effort), so nothing breaks
visibly — but Numu's dashboard and admin tools have no record of which
plan a merchant is on. This is the highest-leverage P0 gap from the
backend roadmap.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Subscription state syncs from Shopify to Numu (Priority: P1)

As the numu-api, when the Shopify-app POSTs a subscription update, I
persist it to a `shopify_subscriptions` table keyed by
`(store_id, shopify_subscription_id)`, return the synced record, and
make it available to other Numu features (dashboard, billing UI, admin
console).

**Independent Test**: from a curl with the internal-key header, POST
the documented body. Assert 200 + the persisted row matches. POST the
same body again — same row updated, no duplicate.

**Acceptance Scenarios**:

1. **Given** no prior subscription record for this `store_id`, **When**
   the Shopify-app posts a sync with `status: ACTIVE`, **Then** a new
   `shopify_subscriptions` row is created with the supplied fields.
2. **Given** an existing `ACTIVE` row, **When** the Shopify-app posts a
   sync with `status: CANCELLED`, **Then** the row's status updates,
   `cancelled_at` is set, no new row is created.
3. **Given** any sync, **When** the row is upserted, **Then** the
   response is `200 {data: SubscriptionResponse, message: "..."}` per
   the existing numu-api response envelope convention.
4. **Given** a request with no `X-Internal-Key` header, **When** the
   route is called, **Then** `403 Forbidden` is returned (the existing
   `verify_internal_key` dependency).
5. **Given** a request with an invalid `plan_id`, **When** the route
   validates the body, **Then** `422 Unprocessable Entity` is returned.

## Requirements

- **FR-001**: System MUST accept `POST /api/v1/shopify/{store_id}/billing/sync`
  with the body shape above. Response: 200 + `SubscriptionResponse` envelope.
- **FR-002**: System MUST persist to a new `shopify_subscriptions`
  table with columns: `id` (UUID PK), `store_id` (UUID, indexed),
  `tenant_id` (UUID, indexed, nullable to match the existing
  `shopify_*` tenant pattern), `shopify_subscription_id` (string,
  unique with `store_id`), `status`, `plan_id`, `is_trial`,
  `trial_ends_at`, `current_period_end`, `cancelled_at`, `synced_at`,
  `created_at`, `updated_at`.
- **FR-003**: Upsert by `(store_id, shopify_subscription_id)` — same
  shopify_subscription_id idempotent.
- **FR-004**: A `GET /api/v1/shopify/{store_id}/billing/subscription`
  endpoint returns the merchant's most recent subscription record (or
  null) so other backend components can read it without re-querying
  Shopify.
- **FR-005**: All responses use the existing `SuccessResponse[T]`
  envelope from `src/api/responses`.
- **FR-006**: Authentication is the existing `verify_internal_key`
  dependency (`X-Internal-Key` header).

## Success Criteria

- **SC-001**: From a curl on staging, sync a Growth subscription →
  `shopify_subscriptions` row appears with `plan_id="growth"` and
  `is_trial=true`.
- **SC-002**: Repeating the same sync produces no duplicate rows.
- **SC-003**: A subsequent CANCELLED sync transitions the same row,
  setting `cancelled_at` and `status="CANCELLED"`.
- **SC-004**: `pytest tests/api/test_shopify_billing.py` green
  including 422-on-invalid-body, 403-on-missing-key, and the upsert
  semantics test.

## Assumptions

- The Shopify-app uses USD pricing exclusively (Shopify Billing API
  enforces this); no currency field needed in the schema.
- `tenant_id` follows the same nullable pattern used in the existing
  `shopify_app_settings` / `shopify_installation` tables.
- Plan IDs match the Shopify-app's `PlanId` literal type
  (`"starter" | "growth" | "scale"`); we validate via Pydantic enum.
- This is a tenant-scoped table; RLS is added in the same migration
  per Constitution Principle V.
