# Phase 0 — Research

Tech decisions, library picks, and integration patterns for marketing-campaigns-v2.

## 1. User-agent → device classification (for FR-016, Sessions by device panel)

**Decision**: Use the `ua-parser` Python library (`pip install ua-parser`) at funnel-event ingest. Classify into 3 buckets: `mobile` | `tablet` | `desktop`. Store the classification on `funnel_events.device` (new column) at write time — not at query time — so the analytics aggregation is a simple GROUP BY.

**Rationale**:
- `ua-parser` is the canonical Python port of the Browserscope UA regex database, ~2M downloads/week, MIT-licensed. Maintained quarterly.
- Computing device at ingest costs ~50µs and runs once per event; computing at query time would force a regex over millions of rows for every panel render.
- Bucket count (3) matches Shopify's pattern and keeps the donut chart legible. Smartwatch / TV / console rarely appear in storefront traffic; collapse into `desktop` (we don't surface a fourth bucket).
- Existing funnel events have NO device column. We'll backfill historical rows with `device=NULL` (acceptable — empty bucket in chart, labeled "Unknown") rather than re-parsing the historical user-agent. Going forward, all new events carry it.

**Alternatives considered**:
- **`user-agents` library**: smaller (no remote regex DB), but parses lazily and is harder to keep current; ua-parser ships with vendored regex updates.
- **Client-side `navigator.userAgentData`**: only available on Chromium; Safari/Firefox would need fallback. Server-side parse wins for consistency.
- **No device tracking, use Cloudflare `cf-device-type` header**: free but coarse (mobile/tablet/desktop), and we'd lose data if we ever leave CF. Not robust to env changes.
- **Compute device at query time**: rejected — 30k+ rows per active store, regex over each row on every dashboard render → 200ms+ panel render. Violates SC-003.

## 2. Auto-match rule storage shape (for US4)

**Decision**: One row per rule. Three columns capture the rule: `field` (enum: `utm_source`/`utm_medium`/`utm_campaign`), `operator` (enum: `equals`/`starts_with`/`contains`), `value` (text). Composition (`combinator: AND|OR`) is per-row; multi-condition rules are modeled as N rows with the same `group_id` UUID. A campaign can have multiple groups; a group's rows are combined per the group's `combinator` field.

**Rationale**:
- Flat-row storage means each row is independently indexed (`store_id`, `field`, `value`) for the ingest-time lookup. JSONB-shaped composite rules would require GIN scans, which are slower than B-tree lookups on common UTM strings.
- The group abstraction handles the "AND/OR" requirement without nesting: rule A AND rule B = two rows sharing a `group_id` with `combinator=AND`; rule A OR rule B = two rows with `combinator=OR`. Future "nested groups" (e.g., (A AND B) OR C) are deferred — single-level groups are sufficient for v1.
- Store-global priority (per the spec assumption) is enforced by a `priority` column unique-per-store; rules are evaluated `ORDER BY priority ASC` until first match.

**Alternatives considered**:
- **JSONB blob per campaign** (`{ rules: [...] }`): rejected — can't index individual conditions, every ingest event scans the full blob.
- **Per-campaign priority instead of store-global**: rejected — allows merchants to game the order; one campaign could trivially intercept all traffic. Store-global with explicit priority is simpler and harder to abuse.
- **Postgres TRIGGER-based stamping**: rejected — couples ingest performance to trigger latency, harder to test, harder to disable for backfill jobs.

## 3. GDPR Recital 47 — verification per Principle II

**Decision**: Document the four required fields in this section. No new GDPR mechanisms needed.

**Legitimate-interest justification** (Recital 47): merchants have a legitimate interest in attributing their own campaigns to their own orders; the attribution data is purely operational (which marketing investment yielded which revenue) and not surfaced to other tenants. Hashing per Principle I is N/A — no cross-store identifiers introduced.

**DSAR path**: a customer's per-order attribution is part of the existing order export. The `orders.campaign_id` FK is a campaign-side identifier (not customer PII); the `orders.attribution` JSONB snapshot already exists from feature 001 and is exported as-is. No new DSAR surface required.

**Erasure path**: the existing customer-redact webhook nulls `orders.customer_id` (a feature-001 behaviour). `campaign_id` references the campaign (not the customer), so it stays — that's correct (the campaign's aggregate stats should still count the redacted order's revenue contribution, the customer's PII is what's erased). The new `campaign_activities` table logs merchant actions only (no customer data); not subject to customer-redact.

**Opt-out effect** (merchant leaves the network): N/A — this is a merchant-internal feature, not a cross-merchant network. When a merchant uninstalls, their `marketing_campaigns` rows + child rules/activities cascade-delete per existing FKs.

## 4. Email open-rate data source (for US9 best-time-to-send)

**Decision**: Use Resend webhook event `email.opened` as the primary signal. Each event includes the original send's `message_id` which we map back to the `MarketingCampaignSend` row (existing table, tracks per-recipient sends). Aggregation: GROUP BY weekday × hour over the store's last 90 days of sends, ranked by `opens_count / sent_count`.

**Rationale**:
- Resend webhook integration already exists in the repo (verified during research). The `email.opened` event includes `message_id`, `delivered_at`, `opened_at`, and `recipient`.
- 90-day window matches the spec's SC-010 + FR-040.
- For stores with no email sends (SMS-only or new), the spec's FR-041 fallback (rank by send count, not open rate) sidesteps the missing-data problem entirely.
- WhatsApp: WhatsApp Cloud API provides `messages.read` webhook events analogous to opens. WhatsApp campaigns live in a separate table (`whatsapp_campaigns`), so per-campaign best-time is a future enhancement. For v1, best-time-to-send applies to EMAIL channel only — SMS and WhatsApp campaigns get the helper text "Not enough open data for suggestions yet" regardless of send history.

**Alternatives considered**:
- **Aggregate at the store level (all-channel)**: rejected — open rates differ wildly by channel (email ~25%, SMS proxy via clicks ~10%, WhatsApp ~70%), aggregating misleads.
- **External engagement-time recommender (e.g., Mailmodo, Iterable AI)**: rejected — external dependency, cost, and the merchant's own historical data is strictly more relevant than a generic model.
- **Precompute on every campaign send (cache)**: defer — recompute at chip-render time is fine for SC-010 (200ms) with the existing 90-day query at single-store scale. If a slow store appears, we add a 1-hr in-memory cache later.

## 5. Backfill performance & locking (for FR-024-FR-028)

**Decision**: Run the backfill as a Celery task (`numu_api.marketing.backfill_campaign_attribution`). Within the task: chunk the work into 5,000-row batches using SAVEPOINT around each batch (matches the existing feature-001 short-code backfill pattern). Update only rows where `campaign_id IS NULL` (preserves FR-025's "skip already-attributed" guarantee idempotently). Wrap in a single transaction per chunk; commit between chunks to keep the transaction short.

**Rationale**:
- The existing migrations for feature 001 established the SAVEPOINT pattern for resilient backfills under concurrent inserts. Reuse it here.
- Chunking at 5,000 rows balances throughput (one round-trip per chunk) against lock duration (each batch holds row locks for <100ms on the indexed columns).
- Idempotency comes for free: `WHERE campaign_id IS NULL` on subsequent runs hits 0 rows. The activity log records `affected_count + skipped_count` so the merchant sees re-runs as "0 affected, X skipped" without confusion.
- Celery task path: enables progress reporting (the UI polls the activity status endpoint) and graceful retry on transient DB errors.

**Alternatives considered**:
- **Inline (synchronous) backfill in the request handler**: rejected — 90-day windows on a busy store can be 500k+ rows; the FastAPI request would time out before completing. SC-007 (30s) leaves no margin for synchronous.
- **No chunking, single UPDATE**: rejected — holds row locks for the duration of the entire backfill, can deadlock with concurrent funnel-event inserts. Constitution requires retry-safe (idempotent) tasks; chunking is the standard practice.
- **Direct `UPDATE ... FROM` joined to campaigns table**: works but loses the per-row decision granularity; harder to count affected vs skipped per chunk.

## 6. Right-sidebar layout pattern (for US3, frontend)

**Decision**: Use shadcn/ui's `Sheet` component for the mobile drawer and a CSS grid (`grid-cols-[1fr_320px]`) for desktop. The right sidebar is a fixed-width column on viewports ≥ 1024px and collapses to a button-toggled drawer below. State of which sidebar section (auto-match / activities / tips) is "open" stays in-component via accordion semantics (shadcn/ui `Accordion`).

**Rationale**:
- shadcn/ui is already vendored repo-wide; no new dependency.
- 320px sidebar matches Shopify's reference layout (per the user's screenshot).
- The CSS grid approach is RTL-safe (`dir="rtl"` flips the column order natively).
- Accordion-based sub-sections (vs always-expanded) keep the sidebar height bounded on small viewports; expansion is per-merchant preference, persisted to localStorage.

**Alternatives considered**:
- **`react-resizable-panels`**: overkill — we don't need user-adjustable widths in v1.
- **CSS variables for the sidebar width**: deferred — fixed 320px is fine.
- **Render the sidebar as a route-level layout component (shared across `/campaigns/:id`)**: deferred — single page benefits, no reuse motivation yet.

## 7. Chart library choice (for US3 chart panels)

**Decision**: Continue using **Recharts** (already vendored at `^2.x` in the merchant hub for existing analytics dashboards). All 8 new panels use Recharts primitives: `<BarChart>` for "by channel/UTM", `<PieChart>` for new-vs-returning + device, `<Histogram>` (custom bin function + BarChart) for "Sales by order", `<LineChart>` for the cross-campaign comparison.

**Rationale**:
- Already in use by `MultiTouchAttributionPage`, `LtvByChannelPage`, `CampaignPerformanceTab`. Switching libraries would orphan those.
- Recharts handles ResizeObserver internally — panels reflow on sidebar collapse without extra wiring.
- RTL support is baked in (`<XAxis reversed>` for RTL locales).

**Alternatives considered**:
- **Tremor.so**: opinionated, would require restyling to match the existing hub design system.
- **D3 direct**: too low-level for the time budget; we'd reinvent legends, tooltips, axis ticks.
- **Apex**: license-incompatible with the hub's package.json constraints.

## 8. Tip heuristic implementation (for US8)

**Decision**: Pure-function service in `src/application/services/campaign_tips.py`. Input: a campaign's already-computed breakdown payloads (channel rollup + coupon stats + device rollup + top_products). Output: ordered list of `{ id, severity, title, body, dismissable: true }`. The function is stateless; ordering is by impact score (e.g., a 5× channel lift is more important than a 2× channel lift, even if both fire the "boost channel" tip).

**Rationale**:
- No LLM call → no latency, no audit risk, no cost, deterministic outputs.
- Pure function → easy to unit test (parameterized matrix of inputs → expected tips).
- Reuses existing aggregation payloads — no new DB queries.
- Sort by impact lets the UI show "top 3" without further ranking logic.

**Alternatives considered**:
- **LLM-generated tips**: deferred — adds cost (~$0.001/render × ~10k renders/day = $300/mo per ad-hoc), latency (~500ms vs <5ms), and audit complexity (every tip needs review). Future enhancement: layer an LLM polish over the heuristic-generated title/body for tone, gated behind a feature flag.
- **Per-tenant tip preferences (mute "boost channel" forever)**: deferred — session-only dismissal is enough for v1 per FR-039.

## 9. Cross-campaign comparison cache strategy (for US7)

**Decision**: No caching in v1. Compute each comparison query on demand. Each campaign's 4 KPIs + 30-day sessions-over-time = 5 queries per campaign × 4 campaigns = 20 queries total per render. With the existing partial indexes, each query is < 50ms on a single-store-sized dataset → 1s total well inside SC-012's 1500ms p95 budget.

**Rationale**:
- Premature caching = bug surface (stale data, invalidation logic). Per spec assumption "Single store at a time", we can revisit when we hit multi-store agency rollout.
- 20 queries × 50ms via `asyncio.gather` = 50ms wall clock (not 1s) — actually well below SC-012.

**Alternatives considered**:
- **Redis-cached comparison payload (60s TTL)**: deferred — adds invalidation logic on campaign edits and a cache-warming task; not justified by query latency.

## 10. Auto-match rule evaluation hot path (for FR-018, FR-019)

**Decision**: At funnel-event ingest, fetch ALL active auto-match rules for the store ONCE per request (cached in a request-scoped `lru_cache` on the funnel ingest service). Evaluate against the incoming event's UTMs in Python — single in-memory pass, ordered by `priority`. Short-circuit on first match. Total overhead per event: ~0.5ms on a store with 100 rules (linear scan, no regex compilation).

**Rationale**:
- Rules change rarely (merchant edits ~10/week), events ingest constantly (~thousands/sec at scale). Cache the rules, scan against them — never the other way around.
- Python `in` / `startswith` operators are 10-100× faster than building a database query for each event.
- Request-scoped cache (not process-wide) avoids stale-rule bugs on rule edits.
- Explicit short_code in URL wins per FR-019: at the start of ingest, check `short_code` first; only fall through to rule eval if no `short_code` matched.

**Alternatives considered**:
- **Database-side evaluation per event** (`SELECT campaign_id FROM rules WHERE field='utm_source' AND value=$1`): rejected — round-trip latency dominates the Python eval cost.
- **Process-wide rule cache with invalidation on rule CRUD**: deferred — request-scoped is simpler, and adds at most 1ms per request (one extra SELECT).
- **Compile rules to a single Aho-Corasick automaton**: overkill for 100 rules.

## 11. Frontend testing strategy

**Decision**: Vitest unit tests for: tip heuristic (TS port of the backend heuristic for client-side validation), auto-match rule editor state, schedule dialog chip rendering. Skip Playwright E2E for v1 — the merchant hub doesn't have a Playwright setup yet, and adding one is out of scope.

**Rationale**:
- Vitest is already configured (used for existing campaign helpers).
- Tip heuristic logic lives in TS for the client-side display only (the backend computes the authoritative tips); a quick unit test catches client/server divergence.
- E2E coverage comes from the manual quickstart (see Phase 1 quickstart.md) until a follow-up feature wires up Playwright.

**Alternatives considered**:
- **Playwright now**: deferred — instrumenting login flow + multi-tenant fixtures is its own multi-day task.

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
| ---- | ---------- | ------ | ---------- |
| Auto-match rule misconfiguration steals traffic from another campaign | M | M | Overlap warning in UI (FR-021) + audit log on rule create + explicit short_code precedence (FR-019) |
| Backfill task runs longer than 30s on a very busy store | L | M | Chunking + SAVEPOINT pattern bounds lock duration; SC-007 is single-store scale. Larger stores get a "took longer than expected" UX hint after 60s but task continues. |
| Device classification slows ingest path beyond budget | L | M | Bench at PR time; if > 200µs/event, move classification to a Celery post-ingest task and accept short delay in panel data |
| Recharts panel renders cause layout thrash on the new layout | M | L | Use `ResponsiveContainer` consistently; throttle resize events; measure with React DevTools profiler before merge |
| Open-rate data sparse on the test env → best-time chips often hidden | H | L | Expected. Test env has few real sends; the helper-text fallback (FR-042) is the planned UX. Production will surface chips once a few campaigns ship. |
| `ua-parser` regex db pinned version goes stale (new browsers misclassified) | L | L | Refresh `ua-parser` minor version every 6 months as a routine deps update; classification accuracy is non-blocking |
