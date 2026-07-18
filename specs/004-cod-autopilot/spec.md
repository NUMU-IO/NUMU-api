# Feature Specification: COD Autopilot for Manual-Shipping Merchants

**Feature Branch**: `004-cod-autopilot`
**Created**: 2026-07-18
**Status**: Draft
**Input**: User description: "COD Autopilot for manual-shipping merchants — automate the full COD order lifecycle (confirm → shipped → delivered+paid) for merchants who ship via external couriers with no API/webhook, using WhatsApp taps from the two humans who know the truth (merchant and customer) plus timer fallbacks."

## Overview

Most NUMU merchants sell cash-on-delivery and ship through external couriers that offer no system integration. Today these merchants advance every order by hand through its lifecycle (pending → confirmed → shipped → delivered), which makes daily operations heavy and error-prone: statuses lag reality, cash collection is untracked, and the cross-merchant trust network receives late or missing delivery signals.

COD Autopilot closes the loop without any courier integration by capturing the two signals that only humans hold — the merchant knows when a package was handed to the courier, and the customer knows when it arrived — each with a single WhatsApp tap, with time-based fallbacks so every order reaches a terminal state even when nobody responds. The merchant's daily operations collapse to one WhatsApp tap plus reviewing a (usually empty) exception queue.

Order confirmation (pending → confirmed) is already automated by the existing customer tap-to-confirm flow and is **not** rebuilt here; this feature automates the two remaining hops: **shipped** and **delivered + paid**.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Customer confirms delivery, order closes itself (Priority: P1)

A customer who received their COD package a couple of days ago gets a WhatsApp message: "Did you receive your order from {store}?" with three buttons — **Received / Not yet / Refused**. They tap **Received**. The order automatically moves to delivered, the COD payment is recorded as collected, the customer sees a short thank-you acknowledgment, and the merchant did nothing at all. The trust network receives a high-confidence successful-delivery signal for that customer.

**Why this priority**: "Delivered" is the step merchants forget most and the one that matters most — it closes the money loop (COD collected) and feeds the trust network. It delivers value even if the merchant still marks "shipped" by hand, so it stands alone as an MVP.

**Independent Test**: Place a COD order for a store with Autopilot enabled, manually mark it shipped, advance the clock past the delivery-check delay, and verify the customer receives the delivery-check message; tap each button and verify the resulting order status, payment status, and recorded signal.

**Acceptance Scenarios**:

1. **Given** a COD order in shipped status for N days (store-configured, default 3) at a store with Autopilot enabled and an opted-in customer, **When** the delivery-check window elapses, **Then** the customer receives exactly one delivery-check message with Received / Not yet / Refused options.
2. **Given** a delivery-check message was sent, **When** the customer taps **Received**, **Then** the order transitions to delivered, its COD payment is marked collected, the transition is recorded as customer-confirmed in the order timeline, and a full-weight successful-delivery signal is recorded for the trust network.
3. **Given** a delivery-check message was sent, **When** the customer taps **Not yet**, **Then** the order stays in shipped status and a follow-up delivery check is scheduled per the retry policy.
4. **Given** a delivery-check message was sent, **When** the customer taps **Refused**, **Then** the order is flagged into the merchant's exception queue and no delivery or payment is recorded.
5. **Given** an order whose customer has not opted in to WhatsApp messages (or has no usable phone number), **When** the delivery-check window elapses, **Then** no message is sent and the order follows the no-response fallback path.
6. **Given** a delivery-check was already answered, **When** the customer taps a button again (duplicate tap), **Then** the system acknowledges without changing the order a second time.

---

### User Story 2 - Merchant marks the whole day shipped with one tap (Priority: P2)

Every day at a store-configured time, a merchant with confirmed orders waiting receives one WhatsApp message listing today's confirmed orders as a numbered list ("1. #1042 — Sara, Nasr City, 450 EGP …") with an **All shipped** button. On a normal day they hand all packages to the courier and tap the button once — every listed order moves to shipped. On a partial day they reply with the exceptions ("except 2, 5"); the listed orders minus the exceptions move to shipped and the excepted ones stay confirmed for tomorrow's digest.

**Why this priority**: This removes the per-order clicking for the "shipped" hop and is the merchant-facing half of the loop. It depends on nothing in Story 1 and is independently valuable, but delivered/paid closure (Story 1) is worth more.

**Independent Test**: Create several confirmed COD orders for an Autopilot-enabled store, trigger the digest, and verify the message content; tap **All shipped** and verify all listed orders became shipped; on a second run reply "except 1, 3" and verify only the others transitioned.

**Acceptance Scenarios**:

1. **Given** a store with Autopilot enabled and ≥1 confirmed manual-shipping COD order, **When** the store's daily digest time arrives, **Then** the merchant's WhatsApp number receives one digest message listing those orders with stable numbering and an all-shipped action.
2. **Given** a store with zero eligible confirmed orders, **When** the digest time arrives, **Then** no digest is sent.
3. **Given** a digest was sent, **When** the merchant taps **All shipped**, **Then** every order listed in that digest transitions to shipped, each timeline entry records the merchant-digest source, and the merchant receives a short confirmation summary.
4. **Given** a digest was sent, **When** the merchant replies with an exceptions message referencing listed item numbers (e.g., "except 2, 5"), **Then** all listed orders except those numbers transition to shipped and the excepted orders remain confirmed.
5. **Given** a digest was sent, **When** the merchant replies with text that cannot be parsed as an exceptions list, **Then** no orders change status and the merchant receives a help message explaining the accepted reply formats plus a link to the orders page.
6. **Given** an order listed in today's digest was already shipped or cancelled through the dashboard before the merchant tapped, **When** the all-shipped action is processed, **Then** the already-moved order is skipped without error and the rest proceed.
7. **Given** a digest response arrives for a digest that was already acted upon, **When** it is processed, **Then** it is acknowledged without re-transitioning any orders.

---

### User Story 3 - Silent orders still close: retries and assumed-delivered fallback (Priority: P3)

A customer never answers the delivery check. The system re-asks up to two more times over the following days. If there is still no answer and the order has been shipped for the store-configured assumed-delivered window, the order auto-closes as delivered with an explicit "assumed delivered (no customer response)" marking — visible to the merchant in the order timeline — and the COD payment is marked collected. Because no human confirmed it, the trust network signal is recorded at reduced confidence (or excluded), so unverified closures never carry the same weight as confirmed ones.

**Why this priority**: Without a fallback, non-responding customers (a large share) would leave orders open forever and the merchant would be back to manual closing — the automation must close 100% of orders, not just the responsive ones. It builds on Story 1's delivery check.

**Independent Test**: Ship an order, let all delivery-check attempts elapse with no customer response, advance past the assumed-delivered window, and verify the order closed as delivered with the assumed-delivered marking, payment collected, and a reduced-confidence (or absent) trust signal.

**Acceptance Scenarios**:

1. **Given** a delivery check with no response, **When** the retry interval elapses, **Then** a follow-up delivery check is sent, up to a maximum of 2 follow-ups per order.
2. **Given** an order with all delivery-check attempts exhausted and no response, **When** the store's assumed-delivered window (measured from shipped) elapses, **Then** the order transitions to delivered with an assumed-delivered source marking and COD payment marked collected.
3. **Given** an order auto-closed as assumed-delivered, **When** trust network signals are recorded, **Then** the successful-delivery signal is tagged low-confidence or omitted — never recorded at the same weight as a customer-confirmed delivery.
4. **Given** an order in the assumed-delivered waiting window, **When** the customer belatedly taps **Received** or **Refused**, **Then** the human answer supersedes the pending fallback (delivered at full confidence, or routed to the exception queue).
5. **Given** an order that reaches the store's existing auto-return (RTO) threshold before the assumed-delivered window, **When** the existing RTO sweep runs, **Then** the RTO outcome takes precedence and no assumed-delivered closure occurs afterward.
6. **Given** an order cancelled or refunded while a delivery check or fallback is pending, **When** the scheduled action fires, **Then** it is skipped and the pending automation for that order is cleared.

---

### User Story 4 - Merchant reviews only the exceptions (Priority: P4)

The merchant opens the Orders page and sees an "Needs attention" view containing only the orders Autopilot could not close on its own: customers who tapped **Refused**, and orders whose delivery checks were exhausted without response (before the fallback closes them). From there the merchant resolves each with existing actions — mark returned, mark delivered, contact the customer, or cancel.

**Why this priority**: The exception queue is what makes "you only touch exceptions" true, but Stories 1–3 already function without it (refused orders are visible via existing status filters and the timeline).

**Independent Test**: Produce one refused order and one response-exhausted order, open the Orders page, and verify both appear in the exceptions view with their reason and can be resolved with existing order actions.

**Acceptance Scenarios**:

1. **Given** orders flagged refused or response-exhausted, **When** the merchant opens the exceptions view, **Then** each appears with its exception reason, age, and order summary.
2. **Given** an exception order, **When** the merchant resolves it (mark returned, mark delivered, cancel, or dismiss the flag), **Then** it leaves the exceptions view and pending Autopilot automation for it is cleared.
3. **Given** a store with no exceptions, **When** the merchant opens the exceptions view, **Then** an empty state confirms all orders are flowing automatically.

---

### User Story 5 - Merchant controls Autopilot from settings (Priority: P5)

A merchant enables COD Autopilot with a single switch in store settings and can adjust: the daily digest send time, the delivery-check delay after shipping, and the assumed-delivered window. Sensible defaults apply so the feature works with zero configuration. Turning Autopilot off stops future digests and delivery checks immediately without touching orders already in flight statuses.

**Why this priority**: Required for controlled rollout and merchant trust, but defaults can be hard-set during a pilot, so it ships last.

**Independent Test**: Toggle Autopilot on with defaults and verify digests/checks flow; change each setting and verify the new value is honored; toggle off and verify no further automated messages are sent.

**Acceptance Scenarios**:

1. **Given** a store with Autopilot disabled (default), **When** orders move through the lifecycle, **Then** no digests or delivery checks are ever sent and no automated closures occur.
2. **Given** Autopilot enabled with no other configuration, **When** the flows run, **Then** documented defaults apply (digest time, 3-day delivery-check delay, 2 follow-ups, assumed-delivered window).
3. **Given** Autopilot is switched off while delivery checks are pending, **When** the scheduled actions fire, **Then** they are skipped and no further automated messages or closures occur.

---

### Edge Cases

- Customer has multiple shipped orders from the same store: each delivery check must unambiguously reference one order (order number in the message), and button taps must resolve to the correct order.
- Customer taps a delivery-check button days later, after the order was already closed (assumed-delivered, RTO, or manually): the tap is acknowledged; a **Refused** tap on an already-closed order surfaces in the exception queue rather than silently discarded, since it contradicts the recorded outcome.
- Merchant's WhatsApp number is missing or has not opted in: no digest can be sent; settings must surface this and the store falls back to dashboard-only operation.
- Digest exceeds message-size limits on high-volume days: the digest caps the listed orders and points to the dashboard for the remainder; capped orders are never silently marked shipped.
- Required message templates lose their approved status: sends are skipped by the existing guard gates; pending orders follow fallback paths and the failure is visible to operations.
- Merchant replies with ambiguous text ("shipped all but the Alexandria one"): treated as unparseable → help message; no status changes on ambiguity, ever.
- Two digest responses race (tap + exceptions reply): first processed response wins; the second is acknowledged as already-handled.
- Order is confirmed after today's digest was sent: it appears in the next digest; it is never appended retroactively to a sent digest.
- An order paid online (not COD) or shipped via an integrated courier: excluded from Autopilot digests and delivery checks entirely.
- Store disables WhatsApp notifications globally while Autopilot is on: existing notification guard gates win; Autopilot sends nothing.
- Assumed-delivered order is later disputed by the customer: merchant can still transition delivered → returned via the existing RTO path; the trust signal correction follows existing RTO signal handling.

## Requirements *(mandatory)*

### Functional Requirements

**Scope & eligibility**

- **FR-001**: The system MUST apply Autopilot only to COD orders at stores with Autopilot enabled, and MUST exclude orders that have an integrated-courier shipment attached (those already receive automated status updates).
- **FR-002**: Autopilot MUST be off by default for every store and controllable per store by the merchant.

**Daily ship digest**

- **FR-003**: The system MUST send each eligible store at most one shipping digest per day, at the store's configured local time, and only when at least one eligible confirmed order exists.
- **FR-004**: The digest MUST list eligible confirmed orders as a stably numbered list including at minimum order number, customer name, delivery area, and COD amount, and MUST offer a single all-shipped action.
- **FR-005**: When the merchant triggers the all-shipped action, the system MUST transition every order listed in that digest (and only those) to shipped, skipping without error any order that already left confirmed status.
- **FR-006**: The system MUST accept an exceptions reply referencing the digest's item numbers and transition all listed orders except those numbers; the referenced exceptions remain confirmed and reappear in the next digest.
- **FR-007**: The system MUST treat any unparseable digest reply as a no-op, respond with usage help and a dashboard link, and change no order status on ambiguity.
- **FR-008**: The system MUST process each digest response at most once; duplicate or late responses MUST be acknowledged without repeating transitions.
- **FR-009**: When the digest would exceed messaging size limits, the system MUST cap the listed orders, state that more orders await in the dashboard, and never act on unlisted orders.

**Customer delivery check**

- **FR-010**: The system MUST send a delivery-check message to the customer of each eligible order a store-configured number of days after it entered shipped status (default 3), offering exactly three responses: Received, Not yet, Refused.
- **FR-011**: On **Received**, the system MUST transition the order to delivered, mark its COD payment as collected, and acknowledge the customer — with no merchant involvement.
- **FR-012**: On **Not yet**, the system MUST keep the order in shipped status and schedule a follow-up delivery check; total delivery-check attempts per order MUST NOT exceed 3 (initial + 2 follow-ups), with a default 2-day spacing.
- **FR-013**: On **Refused**, the system MUST flag the order into the merchant's exception queue without changing delivery or payment state, leaving resolution to the merchant (with the existing auto-RTO sweep as backstop).
- **FR-014**: Each delivery-check response MUST be idempotent: repeated taps acknowledge without re-applying transitions, and a response MUST always resolve to the specific order it was asked about.
- **FR-015**: All delivery-check and digest messages MUST pass the existing WhatsApp send guard gates (store notification toggles, customer opt-in, approved template, per-event idempotent send log); when a send is blocked, the order MUST still progress via the fallback paths.

**Fallback closure**

- **FR-016**: When all delivery-check attempts are exhausted (or could not be sent) with no response, the system MUST close the order as delivered with an explicit assumed-delivered marking once a store-configured window from shipped elapses (default 10 days), marking COD payment collected.
- **FR-017**: A late human response MUST supersede a pending fallback: Received closes at full confidence; Refused routes to the exception queue and cancels the fallback.
- **FR-018**: The fallback MUST yield to terminal outcomes that arrive first (cancellation, refund, manual status change, or the existing auto-RTO sweep) and MUST never reopen or override a terminal state.

**Trust network integrity**

- **FR-019**: Successful-delivery signals recorded for the cross-merchant trust network MUST carry a confidence level derived from their source: customer-confirmed deliveries at full weight; assumed-delivered closures at reduced weight or excluded entirely; timer-based closures MUST never be recorded as full-weight deliveries.

**Auditability**

- **FR-020**: Every automated status transition MUST record its source (customer delivery confirmation, merchant digest action, assumed-delivered fallback) in the order's existing timeline/history, visible to the merchant.

**Exception queue**

- **FR-021**: The merchant dashboard MUST provide an exceptions view listing orders flagged refused or response-exhausted, each with its reason and age, resolvable through existing order actions; resolving or dismissing an exception MUST clear any pending Autopilot automation for that order.

**Configuration**

- **FR-022**: Merchants MUST be able to configure: Autopilot on/off, digest send time, delivery-check delay, and assumed-delivered window; all with documented defaults so the feature functions with zero configuration.
- **FR-023**: Disabling Autopilot MUST immediately stop future digests, delivery checks, and fallback closures without altering the current status of any order.

### Key Entities

- **Delivery check**: Per-order record of the customer delivery-confirmation conversation — attempts sent, next scheduled attempt, response received (received / not yet / refused / none), and final outcome. Drives retries, fallback timing, and idempotency.
- **Ship digest**: Per-store, per-day record of the digest sent to the merchant — the ordered list of order references it contained, the response received (all-shipped / exceptions / none), and processing state. Guarantees a response acts only on the orders that were actually listed.
- **Status transition source**: Attribution attached to each automated order status change (customer-confirmed, merchant-digest, assumed-delivered), stored in the order's existing history and displayed in the timeline.
- **Exception flag**: Marker placing an order in the merchant's attention queue with a reason (refused, response-exhausted, contradicting-late-response) and resolution state.
- **Autopilot settings**: Per-store configuration — enabled flag, digest time, delivery-check delay, assumed-delivered window — living alongside existing store notification settings.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For Autopilot-enabled stores, ≥ 95% of manual-shipping COD orders reach a terminal state (delivered, returned, or cancelled) with zero merchant dashboard interaction after confirmation.
- **SC-002**: The merchant's routine daily shipping workflow requires at most one action per day (the digest response), measured as digest-response rate ≥ 80% of digest days handled entirely via WhatsApp.
- **SC-003**: ≥ 50% of delivery checks receive a customer response within the retry window, and every customer-confirmed delivery closes (status + cash recorded) within 1 minute of the customer's tap.
- **SC-004**: 100% of automated transitions display a human-readable source in the order timeline, and 0 assumed-delivered closures are recorded as full-weight trust-network delivery signals.
- **SC-005**: Median time from shipped to closed for manual-shipping COD orders drops by at least 50% compared to the store's pre-Autopilot baseline.
- **SC-006**: No order is ever left in a non-terminal state past the assumed-delivered window + 1 day while Autopilot is enabled (automation closes 100% of the tail).

## Assumptions

- The existing customer tap-to-confirm flow (pending → confirmed) remains the upstream automation; Autopilot begins at confirmed and does not modify confirmation behavior. Orders confirmed by any means (customer tap, merchant manual) are eligible.
- The merchant's WhatsApp destination is the store's registered contact number; a store without a usable number simply never receives digests (surfaced in settings).
- Customers are reachable at the order's phone number via WhatsApp; non-reachable customers are handled by the fallback path, not treated as errors.
- Two new message templates (merchant digest with action button; customer delivery check with three response buttons) require platform approval before the flows can activate; approval lead time is the schedule's long pole and templates follow the established plain-text (no-emoji) conventions. The customer delivery-check template is a service/utility message, not marketing, so it is not subject to marketing frequency caps.
- Defaults: delivery check 3 days after shipped; up to 2 follow-ups spaced 2 days; assumed-delivered 10 days after shipped; digest at a store-local morning/evening time chosen at enablement. All defaults are per-store configurable (FR-022).
- The existing auto-RTO sweep (store-configured threshold, default 14 days) remains active and takes precedence as the negative-path backstop; Autopilot narrows its workload rather than replacing it.
- The existing WhatsApp notification guard gates (store toggle, customer opt-in, approved-template check, idempotent message log) are reused as-is for all Autopilot sends.
- Existing bulk status-update capability is sufficient for digest-triggered transitions; no new bulk semantics are introduced beyond "the orders listed in this digest."
- Courier-integrated orders (e.g., Bosta auto-shipments) already close via webhooks and are out of scope; the courier-sheet importer and any courier API work are explicitly deferred.
- Multi-tenant isolation, store-local timezones for scheduling, and per-store settings storage follow the platform's existing conventions.

## Out of Scope

- Courier API/webhook integrations (existing Bosta/Mylerz/J&T flows are untouched).
- Courier report/spreadsheet ingestion (deferred to a future feature).
- Any external workflow-automation runtime; scheduling uses the platform's existing background-job system.
- Changes to the order confirmation (pending → confirmed) flow or to trust-network scoring logic beyond adding the delivery-signal confidence attribute.
- Merchant mobile push notifications or channels other than WhatsApp and the existing dashboard.
