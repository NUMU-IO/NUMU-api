# Backend Spec 027: WhatsApp Conversational Approval BOT

**Feature Branch:** `backend-027-whatsapp-conversational-approval`
**Created:** 2026-05-11
**Status:** Draft
**Repo:** `NUMU-api`
**Sibling spec:** `numu-payments-intelligence/specs/016-whatsapp-conversational-approval` (the merchant-side configuration UI; this spec is the inbound-handler + outbound-template backend)
**Input:** Spec 016 (P2): "Many MENA SMBs run their store from a phone, not a dashboard. A BOT that posts risky orders into a merchant WhatsApp group and accepts ✅/❌ replies replicates the dashboard for the dashboardless. Arabic-first."

> **Constitutional alignment:** Principle II (no raw PII in cross-store; we hash phones in the inbound-attribution path); Principle III (legitimate-interest = merchant operations + fraud prevention; DSAR exports include `WhatsAppGroupMessage` rows; erasure on `customers/redact` deletes them within 30 days; opt-out = merchant-side feature toggle disables the bot entirely); Principle V (acceptance scenarios → tests); Principle VI (recovery-first framing in BOT message copy — "Worth confirming?", not "Suspicious order").

## Why this feature exists

A meaningful share of MENA SMB merchants operate their stores entirely from WhatsApp on a phone. They never log into the embedded dashboard. Spec 009 (Recovery Engine) covers the customer side; this spec covers the *merchant* side: when a risky COD order arrives, NUMU posts a card into the merchant's configured WhatsApp group with Approve / Hold / Cancel reply buttons, and the merchant taps once. Decisions are then audited per-staffer (which group member tapped what) and feed back into the existing risk-action pipeline.

The infrastructure already exists:
- WhatsApp client + `MessageLog` (spec 000)
- Risk-scored orders + actions (existing)
- Group messaging via Meta WABA Group API (existing or trivial-add)

What's new:
- A `MerchantWhatsAppGroup` config table (one per store; group_jid + opt-in flag)
- An outbound poster that fires on `RiskAssessmentFinalisedEvent` for risky orders
- An inbound handler that interprets ✅/❌ button-replies (or text "approve"/"hold"/"cancel" + Arabic equivalents)
- Per-staffer audit on `RiskAssessment.action_taken_by` (the WhatsApp sender ID)
- A daily summary template

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Risky order posted to the group (Priority: P1)

As a merchant who configured a WhatsApp group + opted into BOT approval, when a risky COD order arrives, I see a message in my WhatsApp group within 60 seconds with the order number, customer summary, risk score + narrative (from spec 011), and three reply buttons.

**Acceptance Scenarios:**

1. **Given** a `RiskAssessmentFinalisedEvent` with `risk_score >= 60` AND the merchant has `whatsapp_bot_enabled=true` AND a configured `group_jid`, **When** the event consumer runs, **Then** an outbound message is posted to the group with the WhatsApp template `bot_risk_card_{lang}` populated with `{{order_number}}`, `{{amount}}`, `{{risk_score}}`, `{{narrative}}` (from `RiskAssessment.narrative_ar/_en`), and three quick-reply buttons: Approve / Hold / Cancel.
2. **Given** the merchant has not configured a `group_jid`, **When** the event fires, **Then** no message is posted (silent skip; the dashboard is the merchant's intended surface).
3. **Given** the bot is enabled but the group jid is invalid (Meta returns 4xx), **When** the post is attempted, **Then** the failure is logged + the merchant gets a one-time email saying "Your WhatsApp group reference is no longer valid — re-configure in settings." Subsequent risky orders skip the bot send (avoids spamming the email).

### User Story 2 — Inbound reply triggers the action (Priority: P1)

As a staff member in the merchant's WhatsApp group, when I tap the Approve button on a posted card, the underlying order is approved within 5 seconds (Shopify mutation fires; risk assessment's `action_taken` flips to `approved`; my WhatsApp sender ID is recorded as `action_taken_by`).

**Acceptance Scenarios:**

1. **Given** an inbound WhatsApp message reply to a previously-posted bot card, **When** the inbound handler runs, **Then** the matching `RiskAssessment` row is updated: `action_taken='approved'|'held'|'cancelled'` based on the button payload; `action_taken_by=f"wa:{sender_phone_hash}"`; `action_taken_at=now()`. The Shopify Admin GraphQL mutation for the chosen action fires per spec 004.
2. **Given** a button-reply on an already-actioned order, **When** the inbound handler runs, **Then** the BOT replies in the group with "Already actioned by {staffer_label} at {timestamp}" — never re-applies the action.
3. **Given** an inbound message that's NOT a button-reply (free text, sticker, voice), **When** the handler runs, **Then** the message is logged but no action is taken.
4. **Given** a button-reply that arrives after the merchant uninstalls, **When** the handler runs, **Then** the reply is logged + ignored.

### User Story 3 — Daily summary digest (Priority: P2)

As a merchant, at the end of each store-local day, I receive a single WhatsApp message in the group summarizing the day's flagged orders + actions taken.

**Acceptance Scenarios:**

1. **Given** the daily summary task runs at 22:00 store-local time, **When** it computes the day's totals, **Then** a message is posted to the group with: total flagged orders, count by action (approved/held/cancelled/pending), recovered-revenue total. Skips silently if zero flagged.

### Edge Cases

- **Multiple staff tap simultaneously.** First write wins (the unique constraint on `(assessment_id, action_taken_at)` would over-trigger; instead we use a row lock + check `action_taken IS NULL` on the update). The losers see the "already actioned" reply per US2 AS-2.
- **Bot disabled mid-flow.** In-flight cards remain functional (replies still process); new events skip.
- **`customers/redact`.** WhatsApp message logs for the customer's phone are deleted alongside the existing pipeline. Group-side history (Meta-held) follows the existing 90d disclosure per spec 009 CL-010.
- **Customer joins the merchant's WhatsApp group.** Defensive guard: only inbound replies from numbers in the configured `group_admin_phone_hashes` set are honoured. Replies from random numbers are ignored.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** New `MerchantWhatsAppGroupModel` at `src/infrastructure/database/models/tenant/merchant_whatsapp_group.py` with fields: `id`, `tenant_id`, `store_id` (unique — one config per store), `group_jid` (Meta-side identifier), `display_name`, `enabled`, `group_admin_phone_hashes` (JSONB array of HMAC hashes), `last_posted_at`, `last_send_failure_reason`.
- **FR-002:** Migration `20260511_222000_add_merchant_whatsapp_groups.py`.
- **FR-003:** New event consumer `handle_risk_finalised_for_whatsapp_bot` registered in `events/setup.py`. Fires the WhatsApp template post via the existing client.
- **FR-004:** Inbound webhook handler at `POST /api/v1/webhooks/meta/whatsapp/inbound` (or extend the existing one if present) that:
  - Parses the inbound payload
  - If it's a button-reply on a known `MessageLog.message_id`, looks up the linked assessment
  - Verifies the sender's phone hash is in `group_admin_phone_hashes`
  - Atomically updates the assessment + fires the Shopify mutation
  - Replies in the group with confirmation
- **FR-005:** Inbound text-reply parsing: Arabic + English keyword sets like CL-003 from spec 009: `{"approve", "ok", "yes", "حسن", "موافق", "ايوه"}` → approve; `{"cancel", "no", "اوقف", "الغاء"}` → cancel; `{"hold", "wait", "انتظر"}` → hold.
- **FR-006:** Idempotency: each inbound reply is keyed by `wa_message_id`; second deliveries are no-ops.
- **FR-007:** Daily Celery task `tasks.whatsapp_bot.daily_summary` runs at the merchant's store-local 22:00 (defaults to Africa/Cairo when no TZ set).
- **FR-008:** Per Principle II: sender phone numbers are HMAC-hashed before use as `action_taken_by`. Raw sender numbers never persist in `RiskAssessment`.
- **FR-009:** Per Principle III: DSAR includes `MessageLog` rows for the requesting phone (existing pipeline covers this); erasure on `customers/redact` deletes them; opt-out = merchant disables `whatsapp_bot_enabled`.

### Key Entities

```python
class MerchantWhatsAppGroupModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    __tablename__ = "merchant_whatsapp_groups"
    __table_args__ = (UniqueConstraint("store_id", name="uq_wa_group_per_store"),
                       {"schema": "public"})
    store_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    group_jid: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    group_admin_phone_hashes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    last_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_send_failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

## Success Criteria *(mandatory)*

- **SC-001:** Card → group post latency p95 ≤ 60 seconds.
- **SC-002:** Reply → action persisted latency p95 ≤ 5 seconds.
- **SC-003:** Idempotency: 100,000 simulated duplicate inbound messages produce zero double-actions.
- **SC-004:** Per Principle II: zero raw phone numbers in `RiskAssessment.action_taken_by` (always `wa:{hash}`). Quarterly grep audit.
- **SC-005:** Daily summary fires once per store per day (deduped by store_id + UTC date in a beat-task helper table).

## Assumptions

- Meta WABA supports group-message posting + button-template + button-reply parsing in the merchant's region. (Document validated as of 2026-05.)
- The existing WhatsApp client can be extended to call group endpoints without major rework. If not, spec 027a covers the client refactor.
- The merchant configures the group + the admin phone hashes via the Shopify-app settings page (spec 016 frontend).

## Out of scope

- **Voice replies.** WhatsApp voice notes are not parsed in v1.
- **Multi-group support per store.** One group per store in v1.
- **Group analytics dashboard.** Merchant sees the actions in the existing dashboard; no separate "BOT analytics" page.
- **Auto-translation of free-text replies.** Only the curated keyword sets per FR-005.
