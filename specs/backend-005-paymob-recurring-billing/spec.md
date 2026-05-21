# Feature Specification: Paymob Recurring Billing

**Feature Branch**: `backend-005-paymob-recurring-billing`
**Created**: 2026-05-08
**Status**: Draft

## Why this exists

Today `subscribe.py` and `cancel_subscription.py` carry `# TODO: charge
via Paymob recurring API` markers and the recurring side of the
billing loop is dead code. The first-period charge is faked, and
nothing renews subscriptions when `next_renewal_at` arrives — so
active paying tenants would silently keep service for free after their
initial period.

This feature closes the loop: persist the merchant's saved card
token at subscribe time, charge it via the existing
`PaymobPaymentService.charge_saved_token()` for the first period,
and run a Celery beat task that walks due renewals and re-charges the
same token. Failures move the tenant into a dunning state
(`PAST_DUE`) for a short retry window before transitioning to
`READ_ONLY` per the existing tenant lifecycle.

The architecture:

  subscribe POST → first-period `charge_saved_token` → persist
  encrypted token + `next_renewal_at` →
  Celery beat `process_due_renewals` (hourly) →
  re-charge each due token →
  on success: roll `next_renewal_at` forward, write paid invoice →
  on failure: mark `PAST_DUE`, retry up to 3× over 72h →
  exhausted: `tenant_service.transition_to_read_only(reason="payment_failed")`

## User Story 1 — First-period charge actually clears Paymob (P1)

As a merchant subscribing to Starter, when I submit my Paymob card
token, the platform performs a real `charge_saved_token` call against
Paymob. The intent and resulting transaction id are persisted on the
invoice. If the charge fails, the subscription is rejected with a
clear error — no silent activation.

**Independent Test**: with a fake Paymob client returning success,
calling `SubscribeUseCase.execute(...)` writes a `paid` invoice row,
sets `paymob_card_token_encrypted` on the tenant, sets
`next_renewal_at` to now + 30d, and flips lifecycle to `ACTIVE`. With
a fake returning failure, lifecycle stays as it was and the use case
raises.

**Acceptance Scenarios**:

1. **Given** a tenant on TRIAL with a valid card token, **When**
   `SubscribeUseCase.execute(plan="starter")` runs, **Then** the
   recurring service charges the card via `charge_saved_token`, the
   invoice persists with `paymob_transaction_id`, the tenant flips
   to ACTIVE, and the encrypted card token is stored for renewals.
2. **Given** the same call but `charge_saved_token` returns failure,
   **When** the use case runs, **Then** no invoice is created, the
   tenant stays on TRIAL, and `ValueError("payment_failed: …")` is
   raised.

## User Story 2 — Due renewals re-charge automatically (P1)

As the platform, I run a Celery beat task hourly that finds tenants
whose `next_renewal_at <= now()` and `lifecycle_state = ACTIVE`,
re-charges their stored card token for the appropriate plan amount,
and rolls `next_renewal_at` forward by 30 days (monthly) or 365 days
(annual). On failure the tenant moves to `PAST_DUE` with
`renewal_retry_count` incremented; after 3 attempts spanning ≥72h the
tenant transitions to `READ_ONLY`.

**Independent Test**: seed a tenant with `next_renewal_at = now()`,
plug a fake Paymob client into the renewal Celery task, run the task,
assert a new paid invoice exists and `next_renewal_at` advanced.

**Acceptance Scenarios**:

1. **Given** an ACTIVE tenant with `next_renewal_at` in the past,
   **When** `process_due_renewals_task` runs and Paymob returns
   success, **Then** a new paid invoice is created, `next_renewal_at`
   advances by the billing-cycle period, and `renewal_retry_count`
   resets to 0.
2. **Given** the same tenant but Paymob returns failure, **When** the
   task runs, **Then** lifecycle becomes `PAST_DUE`,
   `renewal_retry_count` increments to 1, and `next_renewal_at` is
   pushed +24h for the next attempt.
3. **Given** a tenant in `PAST_DUE` with `renewal_retry_count >= 3`
   AND `subscription_started_at < now() - 72h`, **When** the task
   sees another failure, **Then** the tenant transitions to
   `READ_ONLY` with reason `payment_failed`.

## User Story 3 — Cancel actually clears the recurring state (P1)

As a merchant cancelling, when `POST /billing/cancel` is called, the
platform must clear `paymob_card_token_encrypted` and stop attempting
to renew. The renewal task must skip CANCELLED / READ_ONLY tenants.

**Acceptance Scenarios**:

1. **Given** an ACTIVE tenant with a stored card token, **When**
   `CancelSubscriptionUseCase.execute(...)` runs, **Then**
   `paymob_card_token_encrypted` is cleared, `cancelled_at` is set,
   and the tenant transitions through the existing
   `transition_to_read_only` path.
2. **Given** the renewal task encounters a tenant whose lifecycle is
   `READ_ONLY` or `CANCELLED`, **When** the task runs, **Then** the
   tenant is skipped (no charge attempted).

## Requirements

- **FR-001**: `paymob_card_token_encrypted` (TEXT) and
  `renewal_retry_count` (INTEGER, default 0) MUST be added to
  `public.tenants`.
- **FR-002**: `TenantModel` ORM MUST declare the existing migration
  columns (`paymob_customer_id`, `paymob_subscription_id`,
  `payment_method_last4`, `next_renewal_at`, `billing_cycle`,
  `subscription_started_at`, `cancelled_at`) currently missing from
  the mapping but already on the DB schema.
- **FR-003**: A new `PaymobRecurringBillingService` (under
  `src/application/services/`) MUST expose `charge_subscription(
  tenant, amount_cents, currency, idempotency_ref)` returning a
  typed result: `RecurringChargeSuccess(transaction_id)` or
  `RecurringChargeFailure(reason)`. Stateless; takes a
  `paymob_service` (an `IPaymentService`) plus a
  `secrets_manager` to decrypt the saved token.
- **FR-004**: Card-token storage MUST use the existing
  `secrets_manager` (same path as Paymob credentials at
  `application/external_services/paymob/payment_service.py`) — never
  persist raw tokens. Decryption happens once per renewal attempt.
- **FR-005**: `SubscribeUseCase.execute(...)` MUST call
  `PaymobRecurringBillingService.charge_subscription` for the first
  period when `final_amount > 0`. On failure, raise
  `ValueError("payment_failed: <reason>")` and do not mutate the
  tenant. On success, persist the encrypted token + transaction id.
- **FR-006**: `CancelSubscriptionUseCase.execute(...)` MUST clear
  `paymob_card_token_encrypted` and `paymob_subscription_id` on the
  tenant before delegating to `transition_to_read_only`.
- **FR-007**: A Celery task `tasks.process_due_renewals` MUST run
  hourly, page through `lifecycle_state ∈ {ACTIVE, PAST_DUE} AND
  next_renewal_at <= now()` (limit 100/run), call the recurring
  service for each, and apply the result. Failures within a single
  tenant MUST NOT abort the batch.
- **FR-008**: Renewal failure path: increment `renewal_retry_count`,
  push `next_renewal_at` +24h, set lifecycle to `PAST_DUE`. After
  the 3rd consecutive failure (`renewal_retry_count >= 3`) AND ≥72h
  since the original failed renewal, transition to `READ_ONLY` via
  the existing `tenant_service.transition_to_read_only(reason=
  "payment_failed")`.
- **FR-009**: All charges MUST be idempotent at the Paymob layer by
  passing `f"renewal-{tenant_id}-{period_start.isoformat()}"` as the
  `order_id`. Re-running the task before Paymob's webhook reconciles
  must not double-charge.

## Out of scope (follow-ups)

- Paymob's native server-to-server `appSubscriptionCreate`-style
  recurring API — Paymob does not expose one in MENA today; we
  implement recurring via `charge_saved_token` of the merchant's
  stored card token, which matches their documented flow.
- Email notifications on PAST_DUE / READ_ONLY transitions — that's
  `backend-006` (email delivery).
- Customer-facing dunning UI ("your card declined, please update")
  — this lives in the merchant hub, deferred to a separate spec.
- Annual-billing prorations on plan change — annual cycles renew at
  full price as today; mid-cycle plan-change credits are a separate
  feature.

## Success Criteria

- **SC-001**: `pytest tests/api/test_paymob_recurring.py -v` green.
- **SC-002**: A test simulating a stuck `next_renewal_at` clears
  through `process_due_renewals` end-to-end against an in-memory
  fake Paymob client.
- **SC-003**: Calling `subscribe` twice with the same idempotency
  ref against the same Paymob fake produces only one charge.
