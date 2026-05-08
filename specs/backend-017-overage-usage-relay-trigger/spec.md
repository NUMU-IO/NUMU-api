# Feature Specification: Overage Usage Relay Trigger

**Feature Branch**: `backend-017-overage-usage-relay-trigger`
**Created**: 2026-05-09
**Status**: Draft

## Why this exists

Sprint 1 shipped `UsageRelayService.post_usage` (backend-004) with 9
unit tests. The 2026-05-09 audit then found the relay had **zero
call sites** — overage billing never fired, merchants got unlimited
verifications above cap for free. backend-004 was decorative until
this trigger landed.

## Requirements

- **FR-001**: New Celery task `tasks.report_verification_overages`
  runs daily at 04:00 UTC.
- **FR-002**: Per-plan caps: Starter 450, Growth 5,000, Scale 15,000.
  Defined in code as `PLAN_VERIFICATION_CAPS` so a misconfigured
  registry can't silently re-price overage.
- **FR-003**: For every store with an `ACTIVE` `ShopifySubscription`
  whose `plan_id` is in the cap map, count outbound `message_logs`
  in the current billing period (`current_period_end - 30d` to
  `current_period_end`, fallback `now - 30d` to `now`).
- **FR-004**: When `sent_count - cap > 0`, call
  `UsageRelayService.post_usage(shop_domain, amount_cents,
  description, idempotency_key)` with:
   - `amount_cents = overage * 5` (1 message × $0.05)
   - `idempotency_key = f"{store_id}-{period_start.isoformat()}-overage"`
- **FR-005**: `RelayConfigError` → log + skip (deployment issue,
  ops fixes env). `RelayInvalidPayload` / `RelayUnavailable` →
  log + count as failed. None of these abort the batch.

## Success Criteria

- **SC-001**: `pytest tests/api/test_usage_overage_task.py -v` green.
- **SC-002**: `tasks.report_verification_overages` is in
  `celery_app.tasks` and the beat schedule contains
  `report-verification-overages` with the daily-04:00 cadence.

## Out of scope

- Per-store custom caps (a future custom-pricing tier).
- Real-time at-cap-event triggering. Daily cadence is fine; merchants
  see the charge on their next Shopify invoice anyway.
