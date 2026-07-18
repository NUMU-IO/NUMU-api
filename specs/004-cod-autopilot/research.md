# Research: COD Autopilot (004-cod-autopilot)

**Date**: 2026-07-18
**Input**: spec.md + codebase pattern survey (file references verified against `004-cod-autopilot` branch)

All Technical Context unknowns resolved. Each decision below records what was chosen, why, and what was rejected.

---

## R-01: Scheduling state — dedicated tenant tables, not order.metadata

**Decision**: Two new tenant-scoped tables drive the timers: `whatsapp_delivery_checks` (one row per shipped order under Autopilot) and `whatsapp_ship_digests` (one row per store per day). Beat tasks scan for due rows, cloning the `cod_auto_rto_task` scan pattern (`src/infrastructure/messaging/tasks/cod_auto_rto_task.py:110-210`: `AsyncSessionLocal` + `enable_rls_bypass` for the coarse scan, `narrow_to_tenant` per write, reset in `finally`).

**Rationale**: "Find all checks due before now" and "which digest does this merchant reply belong to" are queries; `order.metadata` JSONB cannot be indexed for that and would force full order scans. Retry counters, response state, and idempotent processing need row-level state with unique constraints.

**Alternatives considered**:
- `order.metadata` flags + scan orders (like `cod_auto_rto_task` does): works for a single sweep, but delivery checks need per-order retry state, scheduled next-attempt times, and response correlation — too much state for metadata, unqueryable.
- Reusing the existing `whatsapp_scheduled_sends` mechanism (`WhatsAppScheduledSendRepository`): it schedules one-shot template sends; it has no concept of response tracking, retries, or outcomes. We reuse its *cancellation* hook where relevant but keep our own state machine.

## R-02: Beat cadence and store-local digest time

**Decision**: No `timezone` column exists on `stores` (`src/infrastructure/database/models/tenant/store.py`). The digest beat task runs **hourly** (`crontab(minute=5)`); each run selects stores whose configured `digest_hour` (0–23, stored in `store.settings.cod_autopilot`) matches the current hour in the store's market timezone — resolved from the existing market registry (store.country → timezone, default `Africa/Cairo`). Delivery-check and fallback sweeps run hourly on the same pattern; the fallback closure check may run daily (03:30 UTC, after the existing RTO sweep at 03:00 UTC so RTO precedence in FR-018 is naturally ordered).

**Rationale**: Hourly beat + in-task hour matching gives per-store local send times without a schema change or per-store beat entries. Ordering the fallback sweep after the RTO sweep enforces "RTO wins" without cross-task coordination.

**Alternatives considered**: adding `stores.timezone` (unnecessary schema change — market registry already maps country→locale conventions); one beat entry per store (unmanageable); fixed platform-wide send time (breaks per-store configurability, FR-022).

## R-03: Inbound button payloads — extend the existing action registry

**Decision**: Extend the established payload convention `"<action>:<subdomain>/<id>"` (parsed by `parse_quick_reply_action` / `_parse_order_id` in `src/application/services/order_confirmation_service.py:23-77`) with four new actions routed from `_action_handlers` in `src/api/v1/routes/webhooks/whatsapp.py:258`:

| Action | Payload | Sender | Meaning |
|---|---|---|---|
| `shipall` | `shipall:<subdomain>/<digest_id>` | merchant | mark all digest orders shipped |
| `dlvyes` | `dlvyes:<subdomain>/<order_id>` | customer | Received |
| `dlvnot` | `dlvnot:<subdomain>/<order_id>` | customer | Not yet |
| `dlvref` | `dlvref:<subdomain>/<order_id>` | customer | Refused |

Merchant free-text exception replies (not buttons) are handled in the `messages` branch: a text message from a phone matching `store.contact_phone` (digits-match via the existing `_phones_match` logic, `order_confirmation_service.py:61`) with an **open digest sent within the last 48h** is parsed as an exceptions list; otherwise ignored by autopilot handlers.

**Rationale**: The webhook route, signature verification, per-handler try/except isolation, and RLS-bypass session (`get_admin_db_session`) already exist and are battle-tested (COD confirm flow). Extending the action set is the minimal-surface change.

**Alternatives considered**: a separate webhook endpoint (duplicates HMAC verify + dispatch); encoding digest item numbers in per-order buttons (Meta caps quick-reply buttons at 3 per template — cannot enumerate orders as buttons).

## R-04: Exceptions-reply parsing — strict grammar, no-op on ambiguity

**Decision**: Accept only: an optional keyword (`except`, `الا`, `ماعدا`, `بدون`) followed by digits separated by `,`/`،`/spaces; both ASCII (`0-9`) and Arabic-Indic (`٠-٩`) digits normalized. Numbers must all be valid item numbers of the open digest. Any other text, out-of-range number, or empty result → no status change + localized help reply with dashboard link (FR-007). Natural-language parsing is explicitly rejected.

**Rationale**: FR-007 mandates "no status changes on ambiguity, ever." A strict grammar is testable and predictable; a fuzzy parser converts merchant typos into wrong shipment states that then trigger wrong customer delivery-checks.

## R-05: Status-source attribution — extend `transition_to` history entries

**Decision**: Add an optional `source: str | None` parameter threaded through `Order.transition_to()` (which today records `{"from","to","timestamp","reason"}` at `src/core/entities/order.py:467`) and `UpdateOrderStatusDTO` / `UpdateOrderStatusUseCase.execute` (`src/application/use_cases/orders/update_order_status.py:66`). Autopilot sources: `customer_confirmed`, `merchant_digest`, `assumed_delivered`. Absent source = manual/dashboard (unchanged behavior). Sources surface in the existing timeline endpoint.

**Rationale**: FR-020 requires auditable attribution; the history dict is additive JSONB, so an extra key is backward-compatible with all existing readers.

**Alternatives considered**: a separate audit table (overkill — timeline already renders `status_history`); overloading `reason` (couples human-readable text with machine attribution; breaks FR-020 testability).

## R-06: Trust-network confidence — assumed-delivered is EXCLUDED from network delivery events (v1)

**Decision**: `_record_network_event` (`update_order_status.py:173`) fires the `delivery` event **only** when the transition source is `customer_confirmed` (or manual merchant action, as today). `assumed_delivered` closures skip the network `delivery` event entirely; the skip is recorded in `order.metadata["network_delivery_skipped"] = "assumed_delivered"` for auditability. No schema change to `network_reputation`.

**Rationale**: Spec FR-019 allows "reduced weight or excluded". The `network_reputation` model aggregates counters per `phone_hash` with a single `confidence_level` column (`network_reputation.py:57`) — there is no per-event weight, and adding one touches the trust network's scoring engine (explicitly out of scope). Exclusion is the only option that guarantees zero poisoning with zero trust-network changes, and it is conservative in the correct direction: a missing positive signal is safer than a fabricated one. RTO events are unaffected (negative path already handled by the RTO sweep).

**Alternatives considered**: per-event `weight` column + scoring change (out of scope per spec; touches `risk_scoring_engine.py`); recording at full weight with a metadata tag (violates FR-019's "never full weight").

## R-07: Fallback closure semantics

**Decision**: The fallback sweep closes orders as `delivered` (source `assumed_delivered`, reason `autopilot_assumed_delivered`) when: Autopilot enabled ∧ delivery-check attempts exhausted or unsendable ∧ `shipped_at + assumed_delivered_days` elapsed ∧ order still `SHIPPED`. It stamps `order.metadata["autopilot_assumed_delivered_at"]` before invoking `UpdateOrderStatusUseCase` (idempotency, cloning `cod_auto_rto_task.py:177`). COD payment marking needs no new code — `transition_to(DELIVERED)` already sets `payment_status=PAID` for COD (`order.py:486-494`). Precedence (FR-018): the sweep skips any order no longer in `SHIPPED`; runs after the RTO sweep; late customer taps supersede pending fallback because the tap handler processes first and flips the delivery-check outcome, which the sweep re-checks per row.

## R-08: Templates — two new entries in the existing registry pipeline

**Decision**: Add to `RICH_TEMPLATES` (`src/core/whatsapp_rich_templates.py:34`), seed via a new Alembic data migration (pattern: `20260601_002000_seed_rich_wa_templates.py`), submit via `scripts/submit_platform_whatsapp_templates.py`:

1. `cod_ship_digest_v1` — **to merchant**, UTILITY, EN+AR. Body vars: store name, order count, numbered order list (single multi-line body variable, capped — see R-09), dashboard link in footer. One QUICK_REPLY button: "All shipped" → `shipall:` payload. (Exceptions arrive as free text; template needs no second button. A second button "Open dashboard" is URL-type — allowed alongside quick-reply only in limited combos, so v1 keeps one quick-reply + link text in body.)
2. `order_delivery_check_v1` — **to customer**, UTILITY, EN+AR. Body vars: store name, order number. Three QUICK_REPLY buttons: Received / Not yet / Refused → `dlvyes:/dlvnot:/dlvref:` payloads.

Both follow the no-emoji rule (Meta rejection 2388060 precedent). UTILITY category avoids the marketing frequency cap (131049 incident).

**Rationale**: Registry → seed migration → submission script is the established three-step template pipeline; send-time construction uses `_build_template_message`'s existing quick-reply component support (`messaging_service.py:231-247`).

## R-09: Digest size cap

**Decision**: Meta template body parameters are limited (1024 chars total body). Cap the digest at **10 orders** per message, ordered oldest-first; the body's final line states "and N more — see dashboard" when capped. Capped (unlisted) orders are excluded from the digest's `order_items` and therefore can never be transitioned by the digest response (FR-009). They roll into the next day's digest.

## R-10: Merchant digest recipient + eligibility guard reuse

**Decision**: Digest recipient = `store.contact_phone` (`store.py:65`); stores with no valid E.164-normalizable contact phone are skipped and the settings endpoint surfaces `digest_deliverable: false`. Sends go through the existing guard (`whatsapp_send_guard.check`) with a new pref key `cod_autopilot_digest` added to `_WA_PREF_KEYS` (`whatsapp_notification_handler.py:43`); customer delivery-checks use pref key `delivery_check` and the standard opt-in/opt-out/template-approved/message-log gates via `_resolve_send_context` (`:229`). Message-log idempotency key: `event_tag = f"digest:{digest_date}"` / `f"dlvcheck:{order_id}:{attempt}"`.

**Rationale**: FR-015 mandates guard-gate reuse; the guard is a pure function taking a prefetched context, so new message types slot in without touching its logic.

## R-11: Exception queue — derived view, no new workflow engine

**Decision**: The exception queue is a **query** over `whatsapp_delivery_checks` (`outcome IN ('refused','response_exhausted','late_contradiction')` and unresolved), exposed as `GET /stores/{store_id}/orders/autopilot-exceptions` returning order summaries + reason + age, and `POST .../autopilot-exceptions/{order_id}/resolve` (body: action `dismiss` — status changes themselves go through existing order endpoints, which clear pending automation via the delivery-check row). Merchant-hub work (rendering the view in `numo-merchant-hub` Orders page) is tracked in the plan but implemented in that separate repository.

## R-12: Settings block

**Decision**: New `store.settings["cod_autopilot"]` section cloning the `cod_trust` settings pattern (`src/api/v1/routes/stores/settings.py:513-560`): defaults dict + Pydantic request/response schemas + `GET/PATCH /{store_id}/settings/cod-autopilot`.

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

Bounds: `digest_hour` 0–23; `delivery_check_delay_days` 1–7; `assumed_delivered_days` 5–30 and MUST be validated ≤ effective `cod_trust.auto_rto_days` is **not** required (RTO precedence handles overlap; see R-07), but the settings response surfaces both values so the UI can warn.

## R-13: GDPR / Constitution Principle II declaration

1. **Legitimate interest / legal basis**: The customer delivery-check is a transactional message completing performance of the sales contract the customer initiated (their own COD order); the merchant digest is B2B operational messaging to the store's own registered contact. Trust-network delivery signals continue under the already-declared Recital 47 legitimate-interest framework — this feature adds **no new** cross-store data flow and *narrows* signal emission (R-06 exclusion).
2. **DSAR path**: `whatsapp_delivery_checks` rows are tenant-scoped and keyed by order/customer phone; they join the existing per-customer export via order linkage. Digest rows contain order references only, no customer PII beyond what orders already hold.
3. **Erasure path**: customer redaction cascades — delivery-check rows for the customer's orders are deleted in the existing erasure flow (add table to the redaction sweep); message logs follow the existing message-log retention rules.
4. **Opt-out effect**: customer WhatsApp opt-out blocks delivery-checks at the guard (order then follows the fallback path); merchant disabling Autopilot stops all sends immediately (FR-023). Network aggregates are unaffected — no new aggregate tables are introduced.

Cross-store hashing (Principle I): the only network write path is the existing `write_network_event` using `extract_phone_hash_from_string` — no new raw PII reaches network scope.
