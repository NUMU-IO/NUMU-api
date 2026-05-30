# Feature Specification: Campaign send maturity — audience targeting + delivery webhooks

**Feature Branch**: `003-campaign-send-maturity`
**Created**: 2026-05-24
**Status**: Draft
**Input**: User description: "Proper audience targeting + Twilio/Resend delivery webhooks + open-rate capture."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Tag-based audience filter (Priority: P1)

A merchant tags their best customers as "VIP" and their newsletter subscribers as "subscribed-newsletter". They want to send an Eid sale preview only to VIP subscribers, not to every customer in the store. Today the campaign blasts to everyone — the audience filter accepts the keys but the resolver ignores them.

**Why this priority**: Untargeted blasts hurt deliverability (carriers throttle stores with high bounce rates) and waste send budget. Tag-based filtering is the cheapest meaningful targeting and unlocks the rest of the work.

**Independent Test**: Tag two customers as "VIP", create an email campaign with `audience_filter: { tags: ["VIP"], match: "any" }`, send. Only the two VIPs receive the email. The third customer (no VIP tag) does not.

**Acceptance Scenarios**:

1. **Given** a store with 100 customers, 10 tagged "VIP", **When** the merchant creates a campaign with `tags: ["VIP"]`, **Then** the audience resolves to exactly the 10 VIPs
2. **Given** a campaign with `tags: ["A", "B"], match: "any"`, **When** the resolver runs, **Then** customers carrying EITHER tag are included
3. **Given** a campaign with `tags: ["A", "B"], match: "all"`, **When** the resolver runs, **Then** only customers carrying BOTH tags are included
4. **Given** a campaign with `tags: ["NONEXISTENT"]`, **When** the resolver runs, **Then** the audience resolves to 0 recipients (no error, just empty)
5. **Given** the merchant types in the tag picker, **When** they see suggestions, **Then** the list is populated from the store's existing tags (no random suggestions)

---

### User Story 2 - Twilio delivery webhook (Priority: P1)

A merchant sends an SMS campaign to 1,000 recipients. Twilio's API accepts all 1,000 immediately, but only ~920 actually reach phones (some numbers wrong, some carrier-rejected). Today the merchant sees `delivered_count: 1000` because the system equates "successfully POSTed to Twilio" with "delivered". They need carrier-confirmed delivery to know which sends actually reached phones.

**Why this priority**: `delivered_count` is the only feedback signal merchants have today; if it lies, they make bad decisions ("our SMS campaigns have 100% delivery, let's keep doing this"). Fixing it unlocks accurate channel analytics.

**Independent Test**: Send a campaign with 3 SMS recipients (one valid, one invalid number, one carrier-blocked). After Twilio's status webhooks fire (~30s), `delivered_count = 1`, `failed_count = 2`, and the per-recipient log rows show the right delivery_status per number.

**Acceptance Scenarios**:

1. **Given** Twilio fires a `delivered` status callback for a known MessageSid, **When** the webhook handler runs, **Then** the matching `marketing_campaign_sends` row flips to `delivered_status=delivered` and the campaign's `delivered_count` increments by 1
2. **Given** Twilio fires a `failed` callback, **When** the handler runs, **Then** the row flips to `delivery_status=failed` and `failure_reason` is populated from Twilio's error code
3. **Given** a webhook arrives with an unknown MessageSid (orphan), **When** the handler runs, **Then** it logs a warning and returns 200 (never errors back to Twilio, which would retry indefinitely)
4. **Given** a webhook arrives with an invalid signature, **When** the handler validates, **Then** it returns 403 and does not process the payload
5. **Given** the same status callback fires twice (Twilio retries), **When** the second arrives, **Then** the row's state is unchanged (idempotent)

---

### User Story 3 - Resend delivery + open webhook (Priority: P1)

A merchant sends an email campaign. Today `delivered_count` never increments for email because that confirmation arrives via Resend's webhook, which we don't have. The merchant also has no open-rate data — feature 002's best-time-to-send chips fall back to "based on send-count habit" because we never capture `email.opened` events.

**Why this priority**: Email is the highest-volume channel for most stores. Without delivery confirmation it's indistinguishable from SMS-without-webhook. Open rate is the single most valuable engagement signal merchants want.

**Independent Test**: Send an email campaign. Resend's webhook fires `email.delivered` → `delivered_count` increments. Open the email → `email.opened` event arrives → the per-send row gets `first_opened_at` and `opens_count` increments. Bounce a known-bad address → `email.bounced` → that customer's email is flagged as bad and excluded from future campaigns.

**Acceptance Scenarios**:

1. **Given** Resend fires `email.delivered` for a known message_id, **When** the handler runs, **Then** the row flips to `delivered` and `delivered_count` increments
2. **Given** Resend fires `email.opened` for a known message_id, **When** the handler runs, **Then** the row's `opens_count` increments and `first_opened_at` is stamped (only on the first open)
3. **Given** Resend fires `email.bounced` (hard bounce), **When** the handler runs, **Then** the customer's `email_is_invalid=true` flag is set and the row is marked failed
4. **Given** Resend fires `email.complained` (spam complaint), **When** the handler runs, **Then** the customer is flagged AND added to a suppression list so they're never re-emailed
5. **Given** a webhook arrives with an invalid signature, **When** the handler validates, **Then** it returns 403
6. **Given** the customer opens the same email 5 times, **When** events arrive, **Then** `opens_count = 5` and `first_opened_at` is the FIRST open's timestamp (never updated)
7. **Given** feature 002's best-time-to-send chips run, **When** there's ≥ 10 days of email send history with open data, **Then** the chips rank by `avg_open_rate` (not `avg_sent`)

---

### User Story 4 - Per-recipient send log (Priority: P2)

The system needs a per-recipient row so webhook handlers (US2, US3) have something to update. Today `dispatch_marketing_campaign` updates only campaign-level counters; there's no per-recipient record.

**Why this priority**: Foundational dependency for US2 + US3 — without per-recipient rows, webhooks can't match incoming events back to a campaign + recipient. P2 because it's a backend-only schema change; merchants don't see it directly.

**Independent Test**: Send a campaign to 5 recipients. The `marketing_campaign_sends` table has 5 rows with `campaign_id`, `customer_id`, `to_address`, `channel`, `message_id` (provider id from Twilio/Resend), `sent_at`. Querying by `(customer_id, sent_at DESC)` returns this customer's send history across all campaigns (feeds the customer-journey timeline from feature 001).

**Acceptance Scenarios**:

1. **Given** a campaign dispatches to N recipients, **When** the dispatch completes, **Then** exactly N rows exist in `marketing_campaign_sends`
2. **Given** the dispatch is interrupted mid-flight (N/2 sent), **When** the dispatch retries from where it left off (by the orphan-rescue sweep), **Then** the N/2 already-sent rows are not duplicated (idempotency key = (campaign_id, to_address))
3. **Given** an ad-hoc transactional email send (not a campaign), **When** the row is created, **Then** `campaign_id IS NULL` is permitted (no FK violation)
4. **Given** the customer-journey timeline endpoint queries this table, **When** it joins to a customer, **Then** their per-send history is visible chronologically

---

### User Story 5 - RFM segment filter (Priority: P2)

A merchant wants to send a retention campaign only to "At Risk" customers (high lifetime value, no recent purchase). They don't want to tag every customer manually — they want the system to compute RFM (Recency, Frequency, Monetary) and bucket each customer.

**Why this priority**: Genuine retention value but more complex than tag filtering. P2 because tags get most stores 80% of the value; RFM is the next tier.

**Independent Test**: A test store has 50 customers with varied order histories. Create a campaign with `audience_filter: { rfm: "at_risk" }`. Resolver returns the customers whose Recency / Frequency / Monetary scores match the "At Risk" segment definition (recent_score=1-2, frequency_score≥3, monetary_score≥3 — i.e., high-value but haven't bought lately).

**Acceptance Scenarios**:

1. **Given** the 6 RFM segments are computed for a store, **When** the merchant filters by `rfm: "champions"`, **Then** the resolver returns customers in the top R+F+M tercile
2. **Given** a store with <10 orders total, **When** RFM is requested, **Then** the resolver returns an empty list (insufficient data) and the create dialog surfaces a warning
3. **Given** an order is placed that changes a customer's segment, **When** the next campaign uses that segment, **Then** the resolver reflects the new segment (within the RFM refresh window — see Assumptions)

---

### User Story 6 - Geographic filter — governorate (Priority: P2)

A merchant in Egypt wants a "Cairo flash sale" only sent to Cairo customers. Governorate is the most granular Egyptian geographic unit relevant to e-commerce.

**Why this priority**: Localized promotions are a real merchant need (delivery zones, regional promotions), but most stores ship countrywide so the demand is moderate.

**Independent Test**: Create a campaign with `audience_filter: { governorate: ["cairo", "giza"] }`. Resolver returns customers whose default address city normalizes to Cairo or Giza.

**Acceptance Scenarios**:

1. **Given** `governorate: ["cairo"]`, **When** the resolver runs, **Then** only customers with a default address governorate of Cairo are included
2. **Given** multi-select `governorate: ["cairo", "giza"]`, **When** the resolver runs, **Then** customers in EITHER are included (OR semantics)
3. **Given** a customer has no default address, **When** the filter runs, **Then** they're excluded (no false positives)
4. **Given** the merchant types in the governorate picker, **When** they see suggestions, **Then** the list is the canonical 27 Egyptian governorates (English + Arabic)

---

### User Story 7 - Purchase-history filter (Priority: P3)

A merchant launching a new variant of a product wants to email previous buyers of the older variant. They want to filter by "purchased product X in last 90 days".

**Why this priority**: Targeted retention is valuable but the merchant can often hack it via tags. P3.

**Independent Test**: Create a campaign with `audience_filter: { purchased: { product_ids: ["abc"], days: 90 } }`. Resolver returns customers with at least one order in the last 90 days containing product "abc".

**Acceptance Scenarios**:

1. **Given** `purchased.product_ids: ["abc"], days: 90`, **When** the resolver runs, **Then** only customers with a matching order in the last 90 days are included
2. **Given** `purchased.collection_ids: ["winter"], days: 30`, **When** the resolver runs, **Then** customers who bought any product in the "winter" collection within 30 days are included
3. **Given** both `product_ids` and `collection_ids`, **When** the resolver runs, **Then** customers matching EITHER (OR semantics) are included
4. **Given** `days: 30` but the order is 31 days old, **When** the resolver runs, **Then** that customer is excluded

---

### User Story 8 - Saved segments (Priority: P3)

A merchant runs a recurring "Cairo VIPs" campaign every month. They want to define this segment once and reference it on every campaign instead of rebuilding the JSONB filter manually.

**Why this priority**: Quality-of-life for power users; not blocking for v1.

**Independent Test**: Merchant creates a segment "Cairo VIPs" with filter `{ tags: ["VIP"], governorate: ["cairo"] }`. They create 3 separate campaigns each referencing this segment via `segment_id`. All 3 resolve to the same audience. Edit the segment definition → all future campaigns referencing it reflect the new filter.

**Acceptance Scenarios**:

1. **Given** a segment "Cairo VIPs" with a defined filter, **When** a campaign references it via `segment_id`, **Then** the resolver uses the segment's filter (not the campaign's `audience_filter`)
2. **Given** the merchant edits the segment's filter, **When** a subsequent campaign uses the segment, **Then** it uses the NEW filter (no stale snapshot)
3. **Given** the merchant deletes a segment in use, **When** a campaign references the deleted id, **Then** the campaign fails to send (status=FAILED, error "segment no longer exists")
4. **Given** a segment with `customer_count_cached: 1234` displayed in the picker, **When** the daily recompute runs, **Then** the cached count is updated to reflect the current matching customer set

---

### User Story 9 - Audience preview (Priority: P3)

A merchant builds a complex filter. Before clicking Send to 50,000 customers, they want to see the resolved count and a sample so they don't accidentally blast to 0 (typo in tag name) or to the wrong cohort.

**Why this priority**: Big mistake-prevention value (sending to 50k by accident is costly + reputationally damaging) but only fires at moment of campaign create.

**Independent Test**: Merchant fills out the filter in the create dialog. The preview pane shows `count: 1,234` and a sample of 10 redacted names (Yahia S., a***@gmail.com, "VIP, Cairo"). Changing the filter updates the preview live (debounced).

**Acceptance Scenarios**:

1. **Given** the merchant types in the filter, **When** they pause for >500ms, **Then** the preview re-fetches and updates the count + sample
2. **Given** the resolved count is 0, **When** the preview renders, **Then** the Send button is disabled with helper text ("Audience is empty — adjust your filter")
3. **Given** the resolved count is > 50,000, **When** the preview renders, **Then** a warning appears: "Large audience — sends to 50,000+ customers may take several hours"
4. **Given** the sample contains PII, **When** the response renders, **Then** emails are partially masked (`y***@gmail.com`) and phone numbers are last-4-only (`+20100***1234`)

---

### Edge Cases

- **Webhook ordering**: Twilio sometimes delivers a `sent` callback AFTER `delivered` (network/proxy reordering). The handler treats terminal states (`delivered`, `failed`, `undelivered`) as sticky — a `sent` arriving after `delivered` is a no-op
- **Resend message_id race**: the Resend SDK returns the `message_id` after the API call returns; if the worker crashes between the API call and the DB insert, the `marketing_campaign_sends` row never gets the `message_id`, and subsequent webhooks for that message land in the orphan-warn path. Acceptable — rare, doesn't break the campaign send
- **Bounced customer in another campaign**: Customer A bounced on Campaign 1 (`email_is_invalid=true` set). When Campaign 2 dispatches, the resolver filters them OUT proactively. No "always permanently bad" flag for soft bounces — only hard bounces flip the bit
- **Suppression list**: A customer who clicks "report as spam" (Resend's `email.complained` event) is added to a per-store suppression list, never re-emailed even if a future filter would otherwise include them. The merchant can manually un-suppress (separate Settings page — out of scope for v1; for now suppression is permanent)
- **Tag with special chars**: Tags with quotes, commas, JSONB-sensitive chars are properly escaped in the resolver query (SEC concern — use bindparam, not string interpolation)
- **Segment cache staleness**: A campaign with `segment_id` reads the LIVE filter from `customer_segments.filter`, not the snapshot at campaign-create time. If the merchant edits the segment between create and send, the latest filter wins (per US8 scenario 2)
- **Audience preview rate limit**: The preview endpoint runs the resolver. Without a rate limit a flaky autocomplete in the UI could spam expensive queries. Limit: 30 req/min per store
- **RFM with zero orders for the store**: Resolver returns empty list; UI shows a warning ("RFM needs at least 10 orders in the store to be meaningful")
- **Customer with no default address but multiple shipping addresses**: For the governorate filter, fall back to MOST-RECENT shipping address's governorate (per data-model invariant — we don't have a "default" flag on addresses today)

## Requirements *(mandatory)*

### Functional Requirements

#### Audience resolver (US1, US5, US6, US7, US8)

- **FR-001**: System MUST resolve `audience_filter.tags: [string]` with `match: "any"|"all"` (default `"any"`); customers carrying matching tags from the store-scoped tag set are included
- **FR-002**: System MUST resolve `audience_filter.rfm: "champions"|"loyal"|"new"|"at_risk"|"hibernating"|"lost"` using the 6-segment taxonomy; segment membership computed from the store's order history with RFM scores
- **FR-003**: System MUST resolve `audience_filter.governorate: [string]` (multi-select) matching customers whose default-or-most-recent shipping address governorate is in the list (case-insensitive, normalized)
- **FR-004**: System MUST resolve `audience_filter.purchased: { product_ids: [], collection_ids: [], days: int }` matching customers who placed at least one order in the last `days` days containing any matching product or any product in the matching collection
- **FR-005**: System MUST support AND-combination across audience_filter keys (a campaign with `tags + governorate + rfm` resolves to customers matching ALL three)
- **FR-006**: System MUST honor `segment_id` when set on the campaign — resolver reads the segment's filter from `customer_segments.filter` at send time (NOT a snapshot at create time)
- **FR-007**: System MUST exclude customers with `email_is_invalid=true` (for EMAIL channel) or `phone_is_invalid=true` (for SMS channel) from every resolver, regardless of filter
- **FR-008**: System MUST exclude customers on the store's suppression list from every email resolver

#### Per-recipient send log (US4)

- **FR-009**: System MUST create one `marketing_campaign_sends` row per recipient at dispatch time before the provider API call
- **FR-010**: Row MUST capture `campaign_id`, `customer_id` (nullable for ad-hoc), `to_address`, `channel`, `message_id` (provider's id, populated AFTER provider API returns), `sent_at`, initial `delivery_status="queued"`
- **FR-011**: Row MUST be unique on `(campaign_id, to_address)` so a re-enqueued dispatch is idempotent (re-dispatching the same campaign doesn't duplicate rows)
- **FR-012**: Send-log rows MUST be queryable per customer for the customer-journey timeline integration (`WHERE customer_id = ? ORDER BY sent_at DESC`)
- **FR-013**: Send-log MUST persist forever (no automatic TTL); a separate retention policy can be defined per Assumptions

#### Twilio status webhook (US2)

- **FR-014**: System MUST expose `POST /api/v1/webhooks/twilio/status` accepting Twilio's standard StatusCallback form payload
- **FR-015**: Handler MUST validate `X-Twilio-Signature` using HMAC-SHA1 against the store's Twilio auth token; invalid sig → 403, no DB write
- **FR-016**: Handler MUST match inbound `MessageSid` to a `marketing_campaign_sends` row and update `delivery_status` (delivered / failed / undelivered / sent) + relevant timestamp (`delivered_at` on delivered, `failed_at` on failed)
- **FR-017**: When a row flips from non-delivered to `delivered`, system MUST atomically increment the campaign's `delivered_count`
- **FR-018**: Handler MUST be idempotent — same callback arriving twice produces no double-count
- **FR-019**: Handler MUST return 200 OK on orphaned MessageSid (no matching row) so Twilio doesn't retry; log a warning

#### Resend events webhook (US3)

- **FR-020**: System MUST expose `POST /api/v1/webhooks/resend/events` accepting Resend's standard webhook payload
- **FR-021**: Handler MUST validate the `Svix-Signature` header against the configured Resend webhook secret (HMAC-SHA256 + timestamp tolerance window); invalid sig → 403
- **FR-022**: On `email.delivered`: update row's `delivery_status` to `delivered` + `delivered_at`; increment campaign's `delivered_count`
- **FR-023**: On `email.opened`: increment row's `opens_count`; if `first_opened_at IS NULL`, stamp it (subsequent opens leave it untouched)
- **FR-024**: On `email.bounced` (hard bounce only): mark the linked customer's `email_is_invalid=true`; mark row failed
- **FR-025**: On `email.complained`: add the recipient to the store's suppression list; mark row failed
- **FR-026**: Handler MUST be idempotent across all event types

#### Audience preview (US9)

- **FR-027**: System MUST expose `POST /stores/{id}/marketing/campaigns/{id}/audience/preview` accepting the filter as request body and returning `{ count, sample: [...] }`
- **FR-028**: Sample MUST be capped at 10 customers with PII redacted (email `y***@gmail.com`, phone `+20100***1234`, name initial-only `Y.S.`)
- **FR-029**: Preview endpoint MUST be rate-limited to 30 req/min per store
- **FR-030**: System MUST return `count > 50000` with a `warning: "large_audience"` flag in the response so the UI can surface it

#### Customer-segments CRUD (US8)

- **FR-031**: System MUST expose CRUD endpoints for `customer_segments` scoped to the store: GET list, POST create, GET single, PUT update, DELETE
- **FR-032**: System MUST validate the segment's `filter` against the same JSONB schema used by `audience_filter` (no schema drift between named segments and inline filters)
- **FR-033**: System MUST recompute each segment's `customer_count_cached` daily via a background task and stamp `last_recomputed_at`

#### Suppression list

- **FR-034**: System MUST maintain a `email_suppressions` table per store: `(store_id, email_address_hash, reason, suppressed_at)`. Address stored as HMAC-SHA256 hash so the table is checkable without leaking PII
- **FR-035**: Email resolver MUST left-join this table and filter out matching addresses
- **FR-036**: Hard-bounce or complaint events MUST add the address to the suppression list; the suppression is permanent in v1 (no auto-expiry)

### Key Entities *(include if feature involves data)*

- **MarketingCampaignSend**: Per-recipient send-log row. Attributes: `id, store_id, tenant_id, campaign_id (nullable), customer_id (nullable), to_address, channel (enum email/sms/whatsapp), message_id (provider id, nullable until provider responds), sent_at, delivery_status (queued/sent/delivered/failed/undelivered/bounced/complained), delivered_at, opens_count (int default 0), first_opened_at, failed_at, failure_reason, suppressed_reason (nullable)`. Unique on `(campaign_id, to_address)` — idempotency. Indexed on `(message_id)` for webhook joins and `(customer_id, sent_at DESC)` for customer-journey timeline.
- **CustomerSegment**: Reusable saved audience filter. Attributes: `id, store_id, tenant_id, name, filter (JSONB), customer_count_cached (int nullable), last_recomputed_at (timestamp nullable), created_by, created_at`. Unique on `(store_id, name)`.
- **EmailSuppression**: Per-store email-address suppression list. Attributes: `id, store_id, tenant_id, email_address_hash (HMAC-SHA256 with PLATFORM_SECRET_SALT per Principle I), reason (enum bounce/complaint/manual), suppressed_at, suppressed_by_user_id (nullable for system-driven)`. Unique on `(store_id, email_address_hash)`.
- **CustomerRFMScore (computed)**: Per-customer RFM scoring view OR materialized table. Attributes: `customer_id, recency_score (1-5), frequency_score (1-5), monetary_score (1-5), segment (champions/loyal/new/at_risk/hibernating/lost), computed_at`. See Assumptions for materialization decision.
- **Existing entities touched** (no schema migration beyond what's listed above):
  - `marketing_campaigns` — no schema change; resolver semantics change
  - `customers` — add `email_is_invalid: bool`, `phone_is_invalid: bool` columns (default false)
  - `customer_touches` (feature 001) — out-of-scope; we don't update touches from send/open events

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A merchant can send a campaign to a tag-filtered audience (e.g. `["VIP"]`) and ONLY tagged customers receive it (validated by manual send to a 3-customer test store; verify per-customer)
- **SC-002**: For an SMS campaign, `delivered_count` reflects carrier-confirmed delivery within 60 seconds of the campaign completing (Twilio's status callback latency p95)
- **SC-003**: For an email campaign, `delivered_count` reflects Resend `email.delivered` events within 60 seconds; `opens_count` reflects `email.opened` events within 5 minutes (Resend's webhook latency p95)
- **SC-004**: Feature 002 US9 best-time-to-send chips now use `based_on: "open_rate"` for stores with ≥ 10 prior email sends with at least one open event captured (instead of falling back to send-count habit)
- **SC-005**: The audience preview endpoint returns `count + sample` in ≤ 800ms p95 for filters that resolve to ≤ 10,000 customers
- **SC-006**: 100% of webhook requests with invalid signatures are rejected with 403 before any DB write
- **SC-007**: 0 duplicate `marketing_campaign_sends` rows when a campaign dispatch is re-enqueued (idempotency invariant)
- **SC-008**: A customer with a hard-bounced email is automatically excluded from all subsequent email campaigns in the same store (no manual cleanup required)
- **SC-009**: A customer who marks an email as spam (Resend `email.complained`) is added to the per-store suppression list within 60 seconds and never re-emailed
- **SC-010**: A merchant can create a saved segment, reference it on 3 separate campaigns, and editing the segment updates all 3 campaigns' resolved audience on the next send
- **SC-011**: 100% of new endpoints respect existing RLS policies (no cross-tenant data leakage in CRUD or preview surfaces)
- **SC-012**: No regression on existing campaign send flow — campaigns with empty `audience_filter` (or `{all: true}`) continue to resolve to "all customers with email/phone NOT NULL" (backwards compatible)
- **SC-013**: Webhook handlers process inbound events at ≥ 100 req/sec without backpressure (single test-env instance) — required because Resend can fire a burst of events when a large send completes

## Assumptions

- **RFM materialization**: We materialize a `customer_rfm_scores` table refreshed nightly via a Celery beat task. On-the-fly computation per query is too expensive for stores with > 10k customers; nightly is freshness-acceptable for retention campaigns (the segment doesn't shift hourly). The materialization task uses standard RFM quintile scoring (recency = days since last order, frequency = order count, monetary = total spend over 90 days).
- **Send-log retention**: Forever (no TTL) in v1. The table is per-recipient, growing ~1 row per email/SMS sent — at typical scale this is comfortable for years. A retention policy can be added as a separate feature when storage cost becomes material.
- **Webhook secret rotation**: One global Resend / Twilio webhook secret per environment (dev/test/stage/prod), stored as `RESEND_WEBHOOK_SECRET` and `TWILIO_AUTH_TOKEN` env vars (Twilio's auth token doubles as the webhook signing key per their docs). Rotation is a manual ops procedure; the env vars get updated, all running workers pick up the new value on restart.
- **Audience filter combinators**: AND-of-all keys at the top level (e.g., `{ tags: [...], governorate: [...] }` resolves to "tagged AND in governorate"). No nested AND/OR like feature 002's auto-match rules — simpler mental model, sufficient for v1. If merchants need OR-of-conditions, they create two segments and run two campaigns.
- **Open tracking pixel**: Resend handles pixel injection automatically. We do NOT add a per-store opt-out for v1 (GDPR strict-mode for opens is a separate compliance feature). Implicit consent because the merchant created the campaign and the customer is on the store's existing list.
- **Hard-bounce policy**: Hard bounces (5xx SMTP) flip `email_is_invalid=true` PERMANENTLY for the customer. Soft bounces (4xx) do NOT flip the flag — they're retryable failures and the next campaign will try the address again. Hard-bounce is irreversible without manual customer-record edit; this matches industry-standard practice (Resend, Mailchimp, etc.).
- **Suppression scope**: Per-store, NOT per-tenant. Two stores under the same tenant don't share suppression — Customer A complaining about Store X doesn't affect Store Y's ability to email them (the relationship is store-to-customer, not tenant-to-customer).
- **Egyptian governorate canonical list**: 27 governorates per Egypt's administrative divisions. List maintained as a constants file; multi-language labels (Arabic + English transliteration) included.
- **No webhook-secret-per-store**: Resend's webhook config is global (one secret per Resend app). We don't fragment per store. This is fine because the message_id → store_id mapping is established at send time via the per-send log; an attacker who somehow knew the global secret still couldn't forge an event matching a real message_id.
- **Multi-tenant rate-limit on preview**: Per-store, 30 req/min. Implemented via the existing Redis sliding-window rate-limit middleware.
- **PII redaction in preview sample**: Email: keep first char + domain (`y***@gmail.com`); phone: keep country code + last 4 (`+20100***1234`); name: initials only (`Y.S.`). Tags: full visibility (they're merchant-set, not PII).
