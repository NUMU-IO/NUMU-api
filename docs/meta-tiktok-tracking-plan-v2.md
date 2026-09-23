# Meta & TikTok tracking — root causes and the plan to reach 9/10 (v2)

**Date:** 2026-09-23
**Supersedes:** the v1 investigation of 2026-09-21
**Status:** built, in review (2026-09-23). §0 lists what shipped and where the build departed from this plan.

---

## 0. Implementation status

Each phase is a stacked PR per repo. Merge in phase order.

| Phase | API | Storefront | Hub |
| --- | --- | --- | --- |
| 0 — stop the loss | #668 | #170 | — |
| 1 — one Purchase per order | #670 | #172 | #349 |
| 2 — identity | #671 | #173 | — |
| 3 — COD delivered signal | `feat/tracking-phase3` | — | `feat/tracking-phase3` |
| 4 — monitoring + cleanup | `feat/tracking-phase4` | — | `feat/tracking-phase4` |

**Where the build departed from the plan**

- **RC1 / G1: no `customer_touches` migration.** The attribution envelope keeps its 256-character click-id cap, because it must fit a 4 KB cookie. The full `ttclid` reaches TikTok through the dedicated `ttclid` cookie instead, and `/track` stops forwarding an envelope id cut at the cap. A test guards that schema caps fit their columns.
- **RC2 is wider than written.** The `Order` entity path fails too (G4). The fix is one `order_view()` normaliser inside the builders, not a conversion to the entity.
- **RC7: no outbox migration.** The TikTok rail already wrote the row before the POST and had replay tasks (`0f48c8f4`).
- **RC9: TikTok `state` is not a field.** TikTok's `user` object has no `state` key; an existing test records this. That part of RC9 was wrong.
- **RC10 / D1.** The order snapshot uses the first `X-Forwarded-For` hop (same rule as `/track`). COD fraud scoring keeps the connection IP. No new env var.
- **RC13 not built.** The storefront cannot see hub or admin sessions, which live on other domains.
- **D4 decided by the owner: no consent gate.** Phase 2 enrichment runs for every shopper. The existing per-store `consent_required` toggle (off by default) is untouched, and its opt-out is still honoured end to end. §10's legal exposure stands.
- **Already existed, so not rebuilt:** Meta batch poison isolation, TikTok replay, Meta outbox counts in the status endpoint, and the Meta Dataset Quality poll with snapshot history.
- **Skipped:** the synthetic canary (G-7). The daily gap alert plus the regression tests cover the same failures.
- **Phase 3 routing.** The delivered conversion and the Meta Refund hook into the two shared, idempotent paths, `emit_order_delivered` and `try_restock_order`. Courier webhooks do not publish status events: that would re-trigger customer emails and WhatsApp messages.
- **Purchase timing.** Purchase fires at placement (manual rails on approval). The per-store `purchase_trigger` has no remaining effect, so its hub selects were removed.

**Merchant setup to use Phase 3**

- Meta: create a custom conversion on the `OrderDelivered` event.
- TikTok: create an Offline Event Set, paste its id in Settings → Tracking → TikTok, then optimise on its Purchase.

---

## 1. Summary

Vionne's TikTok-ad traffic is almost invisible to TikTok because **every browser
tracking event from a TikTok-ad visitor is rejected by our own API**: the click id
(`ttclid`) is longer than the 256-character cap on `TrackPageViewRequest.ttclid`,
FastAPI rejects the whole request with 422, and the storefront proxy answers 204
regardless, so the failure is silent. Where the click id *is* stored, it is
truncated to 256 characters, which makes it unmatchable at TikTok.

A second defect compounds it: both order-based purchase dispatchers read
`order.metadata`, which on a SQLAlchemy `OrderModel` is the declarative
`MetaData` registry, not the order's JSON column (`extra_data`). Every caller
swallows the resulting `AttributeError`, so the TikTok recovery sweep, the
status-driven Purchase/Lead/Refund, the Fawry webhook path and the thank-you
enrichment are all dead. The domain `Order` entity, which every other payment
and courier webhook passes, fails too: the builders call `.get` on its Pydantic
address and line items. No order-based Purchase reaches either platform on any
path except the Meta hourly sweep.

A third defect means every order-based event tells Meta and TikTok that all
shoppers share one IP address and a Node.js user agent.

Together these explain both the missing TikTok attribution and the low event
match quality. Phase 0 of this plan fixes them in 1–2 days. Phases 1–4 (about
6–7 weeks in total) take NUMU past what Shopify, Zid and Salla do natively;
Phase 5 holds optional later work. §8 lists every phase with its tasks, the
decisions it needs (each with a default) and its exit criteria.

**What "9 out of 10" realistically means.** Vendor benchmarks put 8–9 on
checkout and Purchase events and 6–7 on pre-checkout events. Anonymous browsing
has no email or phone to send, so 9+ is realistic for Purchase and checkout
events; for browsing events the honest target is 7+, and only after the identity
enrichment in Phase 2.

---

## 2. Evidence base

| Source | What it covers | Freshness |
| --- | --- | --- |
| `NUMU-api` `origin/prod` @ `49cb4f9f`, `numu-storefront` `origin/prod` @ `b7973b1` | Every code claim in this document | Re-verified 2026-09-22 |
| Read-only SQL against production (`READ ONLY` transaction, RLS-enforced app role, owner-approved) | Vionne's 31 orders since 2026-07-22, `tiktok_event_log`, `meta_event_log`, `funnel_events`, `page_views` | Captured 2026-09-21, **not re-run for v2** |
| TikTok Ads first-party MCP: advertiser `7665421777341857812`, pixel `D9GH5NRC77U5KEVKREF0` | Campaign reports, pixel settings | 2026-09-21 |
| Official Meta / TikTok / WebKit documentation | §6 platform rules | 2026-09-23 |
| Shopify, Zid, Salla documentation and help centres | §7 comparison | 2026-09-23 |

**Not available:** Meta's own EMQ numbers (the meta-ads MCP needs
re-authentication), and TikTok per-event EMQ (catalog diagnostics return
`40001`; TikTok publishes no match-quality API at all — see §6).

Only order numbers, flags, lengths and counts were printed from the database.
No names, phone numbers, email addresses, IP addresses or click-id values were
read.

---

## 3. What changed from v1

v1 diagnosed the problem correctly. Re-checking it against current production
code found **three gaps that would have broken or neutralised Phase 0**.

| # | Gap in v1 | Evidence | Consequence if shipped as written |
| --- | --- | --- | --- |
| **G1** | The RC1 fix has no database migration | `customer_touches.gclid/fbclid/ttclid` are `String(256)` (`src/infrastructure/database/models/tenant/customer_touch.py:60-62`, created in `alembic/versions/20260522_030000_add_customer_touches_table.py:84-86`) | Raising only the Pydantic caps moves the failure from a 422 to a database `StringDataRightTruncation` on the attribution-touch insert |
| **G2** | The RC4 fix does not work | `src/api/v1/routes/storefront/checkout.py:790` reads `X-Real-IP` **first**, and nginx sets `X-Real-IP $remote_addr` on every location (`docker/nginx/nginx.conf:128`) — that is the storefront server | Forwarding `X-Forwarded-For` from the checkout proxy changes nothing; orders keep storing the server's IP |
| **G3** | The RC2 impact list misses Fawry | `src/application/services/fawry_webhook_service.py` loads an `OrderModel` and passes it to both dispatchers | Fawry-paid orders have never produced a server Purchase on either platform |
| **G4** | RC2 is wider than v1 said: the `Order` **entity** path fails too | Builders call `.get` on `OrderShippingAddress` / `OrderLineItem` Pydantic objects; reproduced locally | Every payment and courier webhook Purchase (Kashier, Fawaterak, Moyasar, Paymob, Bosta, Mylerz, J&T) is dead as well. v1's "convert to entity" fix would have made it worse |

Everything else in v2 is additive:

- A security caveat on the IP fix (RC10): the first `X-Forwarded-For` hop is
  client-controlled, and the same value feeds COD fraud scoring
  (`checkout.py:3057`).
- Manual rails (InstaPay, Vodafone Cash) fire Purchase on **proof approval**,
  not at order creation, so unpaid or abandoned orders are not reported as
  sales.
- Consent is not carried on order-based events (RC12).
- The internal-traffic filter does not detect merchant, staff or impersonation
  sessions (RC13).
- Meta batch behaviour: one invalid event rejects an entire batch of up to 1,000
  (§6), and our sweep sends batches of 100.
- A guardrails section (§9): schema-vs-column test, golden payload tests,
  synthetic canary, token-expiry alerts, replay tool.
- A comparison with Shopify, Zid and Salla (§7) and the platform rules that
  constrain the design (§6).
- Compliance (§10): Egypt's PDPL and consent.
- The daily "orders but no server Purchase" alert moves from Phase 4 into
  Phase 0.

---

## 4. Root causes

RC1 alone explains why TikTok sees almost nothing from its own ad traffic. RC1,
RC2 and RC4 together explain most of the low match quality.

### RC1 — a `ttclid` longer than 256 characters makes the API reject every browser event · CRITICAL

**What happens**

1. TikTok appends `ttclid` to the landing URL. The storefront saves it,
   untruncated, in a 30-day cookie
   (`numu-storefront/src/lib/tiktok-pixel.ts`, `ensureTtclidCaptured`).
2. On every browser event the `/api/storefront/track` proxy copies the decoded
   cookie into `body.ttclid`
   (`numu-storefront/src/app/api/storefront/track/route.ts:117-119`).
3. The API model caps the field:
   `ttclid: str | None = Field(None, max_length=256)`
   (`src/api/v1/routes/storefront/tracking.py:397`). FastAPI rejects the **whole
   request** with 422.
4. The proxy returns 204 whatever the upstream said ("Always 204 to the SDK. We
   don't care what the upstream said"). The rejection is invisible.

Two other writers truncate rather than reject:

- the attribution cookie (`src/lib/attribution-client.ts:41`,
  `CAP_CLICK_ID = 256`, applied at `:109-111`);
- the checkout snapshot (`checkout.py:821`, `[:256]`).

A truncated click id matches nothing at TikTok.

**Evidence**

- All 7 TikTok orders store a `ttclid` of exactly 256 characters, in the order
  snapshot and in both attribution touches. Two independent writers both hit
  their cap, so TikTok's real ids are longer than 256.
- Reproduced locally against an identical copy of the production model: 256
  characters is accepted; 257 and 320 are rejected with
  `String should have at most 256 characters [type=string_too_long]`.
- The TikTok-ad sessions have no browser-originated rows at all (§5). Control
  sessions have full funnels.
- `tiktok_event_log` has zero server events carrying a `ttclid` since
  2026-09-01.
- TikTok's own documentation specifies **no maximum length** for `ttclid`. The
  256 cap is ours, not theirs.

**Effect.** TikTok's Events API never receives any event from a TikTok-ad
visitor: no ViewContent, AddToCart, InitiateCheckout or Purchase. TikTok sees
only its browser pixel, which ad blockers, in-app browsers and ITP all reduce.
Meta loses the same visitors' server events, because one request feeds both
platforms. NUMU's own analytics are blind to TikTok-ad traffic: sessions, funnel,
landing pages and Live View.

**Fix**

- Raise all three caps to 2048 and stop truncating. A click id is only useful
  byte-for-byte.
- **Widen the database columns (G1)** in the same migration.
- Make `/track` lenient on optional identifiers: a `mode="before"` validator
  that nulls an oversized optional field, so one bad field never rejects the
  whole event.
- Make the proxy count and log upstream non-2xx responses (including the first
  error `loc`, e.g. `body.ttclid string_too_long`) while still answering 204 to
  the browser.
- Apply the same review to `fbc`, `fbp` and `referrer` (500).

### RC2 — `order.metadata` crashes the order-based purchase builders · CRITICAL

**What happens.** `src/application/services/tiktok_capi_purchase_dispatcher.py:66`
and `src/application/services/meta_capi_purchase_dispatcher.py:92` both run
`meta = getattr(order, "metadata", None) or {}` and then `meta.get(...)`. On an
`OrderModel`, `metadata` is SQLAlchemy's declarative `MetaData` registry; the
order's JSON column is `extra_data`
(`src/infrastructure/database/models/tenant/order.py:173`). So `.get` raises
`AttributeError: 'MetaData' object has no attribute 'get'`.

**Callers that pass an `OrderModel`** — all of them catch the exception and log
it, so nothing surfaces:

| Caller | Location |
| --- | --- |
| TikTok orphan sweep | `src/infrastructure/messaging/tasks/tiktok_capi.py:851` |
| Meta / TikTok status handlers (Purchase, Lead, Refund) | `src/infrastructure/events/handlers/meta_capi_status_event_handler.py:80` |
| `/track` order enrichment | `src/api/v1/routes/storefront/tracking.py:1365` |
| **Fawry payment webhook (G3)** | `src/application/services/fawry_webhook_service.py` |

**The entity path is broken too (G4, found 2026-09-23).** The other payment
and courier webhooks (Kashier, Fawaterak, Moyasar, Paymob, Bosta, Mylerz, J&T)
pass the domain `Order` entity from `OrderRepository`. The entity has a
`metadata` dict, but its `shipping_address` is a Pydantic
`OrderShippingAddress` and its `line_items` are Pydantic `OrderLineItem`s. The
builders call `shipping.get(...)` and `li.get(...)`, so the entity path raises
at a different line. Reproduced locally against `origin/dev` with a real
`OrderModel` and a real `Order`:

| Builder | `OrderModel` | `Order` entity |
| --- | --- | --- |
| TikTok `_build_user_data_from_order` | `'MetaData' object has no attribute 'get'` | `'OrderShippingAddress' object has no attribute 'get'` |
| Meta `_build_user_data_from_order` | `'MetaData' object has no attribute 'get'` | `'OrderShippingAddress' object has no attribute 'get'` |
| TikTok `_build_custom_data_from_order` | works | `'OrderLineItem' object has no attribute 'get'` |

So **no order-based Purchase reaches either platform on any path.** The only
exception is the Meta sweep, which wraps the raw JSONB columns in a
`SimpleNamespace` adapter (`tasks/meta_capi.py:2077`) that happens to match
what the builders expect.

**Evidence**

- TikTok sweep rows (created at `hh:25`) appear until 2026-09-07 08:25 UTC and
  never again. Commit `0f48c8f4` ("harden the TikTok Events API rail"), promoted
  2026-09-08, changed the sweep to hand off to the dispatcher.
- Vionne's `purchase_trigger` is `confirmed` on both platforms. Orders
  ORD-472583, ORD-580571 and ORD-831003 were confirmed on WhatsApp; none has a
  status-driven Purchase row on either platform.
- The unit tests pass because they substitute a `SimpleNamespace`
  (`test_track_order_completed_enrichment.py`). No test passes a real
  `OrderModel`.

**Effect.** No TikTok Purchase is recovered for any order that misses the
thank-you page. No status-driven Purchase, Lead or Refund reaches either
platform, so cancelled and returned orders are never reversed on Meta. Fawry
orders have never sent one. The order-based enrichment shipped 2026-09-07 has
never run.

**Fix.** One idempotent `order_view(order)` normaliser in
`meta_capi_purchase_dispatcher.py`. It exposes `metadata` (from `extra_data` on
the model), `shipping_address` and `line_items` as plain dicts, and reads every
other field through to the wrapped order. Call it at the start of each shared
builder (both `_build_user_data_from_order`, both
`_build_custom_data_from_order`, `resolve_catalog_ids`). That fixes all callers
in one place, and the Meta sweep's hand-built adapter can be deleted.
Converting everything to the entity instead would not work: the entity is
exactly the shape the builders cannot read. Add one test per builder with a
real `OrderModel` and a real `Order` entity.

### RC3 — orders that skip the thank-you page have no working TikTok path · HIGH

InstaPay and Vodafone Cash render their payment instructions inline; merchant-,
WhatsApp- and DM-created orders never touch the storefront. Their only possible
TikTok Purchase is the sweep (dead — RC2) or a courier "COD collected" webhook,
which is skipped when the merchant already marked the order paid in the hub.

**Fix.** Send the server Purchase from the order, not the page:

- **COD and card orders** → at order creation, post-commit, from the checkout
  request (the one moment that has the real IP, user agent, cookies and full
  `ttclid`), with `event_id = order.id` so the browser copy deduplicates
  against it.
- **Manual rails (InstaPay, Vodafone Cash)** → on `PaymentProofApprovedEvent`
  (`src/core/events/payment_events.py:21`, published alongside `OrderPaidEvent`
  by `review_payment_proof.py:186-210` and the auto-approve path in
  `submit_payment_proof.py:556-570`). Sending these at creation would report
  unpaid and abandoned orders as sales — ORD-744686 was cancelled and replaced,
  and would have counted. The order snapshot still carries the real identity, so
  no signal is lost by waiting.

### RC4 — the checkout snapshot stores the server's IP and user agent · HIGH

The storefront's `/api/checkout` proxy forwards only `cookie`, `x-numu-csrf` and
`idempotency-key` (`numu-storefront/src/app/api/checkout/route.ts:25-33`). The
API then reads the client IP from the proxy connection and the user agent from
Node's `fetch` (`checkout.py:790-802`) and stores both on the order
(`checkout.py:1931-1938`). Every order-based event reads them back.

**Evidence.** 20 Vionne orders carry a snapshot: 1 distinct IP, 1 distinct user
agent, all 20 non-browser (Node).

**Effect.** Every Meta sweep Purchase and every courier-webhook Purchase on both
platforms claims the same server IP and a bot-like UA for every shopper. Meta
ranks IP and user agent as core match keys, and a shared server IP is a negative
signal.

**Fix.** Forward `X-Forwarded-For`, `CF-Connecting-IP` and `User-Agent` from
`/api/checkout`, exactly as `/track` and the cart-add path already do — **and
(G2) stop preferring `X-Real-IP` in `checkout.py:790`**. Share one client-IP
resolver with `/track` (`tracking.py:440`) so the two paths cannot drift again.
See RC10 for the trust boundary.

### RC5 — the `purchase_trigger` design does nothing · HIGH

The trigger fires only when `new_status == purchase_trigger` (Meta handler
`:101`; TikTok handler `~:104`). The order entity allows `pending → processing`,
and the hub's workflow is `pending → processing → shipped → delivered`. Only the
WhatsApp customer confirmation emits `confirmed`. Courier webhooks call
`order.deliver()` without publishing a status event. The storefront cannot see
the trigger (public settings strip it), so the thank-you page sends Purchase at
placement anyway.

**Decision (recommended).** Keep Purchase at placement, deduplicated between
browser and server, because both platforms need the volume — and add a separate
server-only delivered/confirmed signal (Phase 3). If a delayed Purchase is still
wanted instead, it must fire when the order reaches **or passes** the trigger
status (rank-based and idempotent), courier webhooks must publish status events,
and the placement copy must be suppressed.

### RC6 — thin identity before checkout · MEDIUM (largest EMQ drag after RC1)

- TikTok ViewContent carries `_ttp` on only 28% of events, because NUMU loads
  TikTok's script only after the first interaction.
- Email and phone appear on under 3% of events before checkout.
- `ttclid` is on 0% of events (RC1).
- `_IDENTITY_RESOLUTION_STEPS` (`tracking.py:102`) runs the database lookup only
  from `checkout_started` onward; earlier events get only the Redis cache
  (`_resolve_session_identity`, `tracking.py:1217-1280`).

### RC7 — TikTok has no delivery outbox · MEDIUM

`meta_event_log` gained a full lifecycle on 2026-08-18
(`alembic/versions/20260818_010000_meta_event_log_outbox_lifecycle.py:67-101`):
`status`, `next_retry_at` (also the claim lease), `expires_at`, `priority`,
`failure_kind`, plus two partial delivery indexes; with `DeliveryStatus` and
`FailureKind` in `src/core/services/meta_delivery_policy.py:62-124`.
`tiktok_event_log` has the same shape **minus** all of it
(`src/infrastructure/database/models/tenant/tiktok_event_log.py:35-109`). The
log row is written after the broker call, enqueueing is a plain `.delay`, and
once Celery's retries are spent nothing re-drives the event. The sweep also
counts failed rows as sent.

**Fix.** Generalise Meta's table with a `platform` column rather than cloning
it. The column overlap is nearly total; only the vendor trace field differs
(`fbtrace_id` vs `request_id`/`response_code`), which folds into one nullable
`vendor_trace_id`. `tiktok_delivery_policy.py:104-118` already classifies
`INVALID_CREDENTIALS`, so the policy layer is ready.

### RC8 — TikTok's `external_id` does not match across the funnel · MEDIUM

`_first_external_id` (`src/infrastructure/external_services/tiktok/hashing.py`)
prefers `customer_id`, so the order-based Purchase sends `sha(customer_id)`
while earlier events and `ttq.identify` send the session fingerprint. The
Purchase does not join the funnel that preceded it.

**Fix.** TikTok's `external_id` accepts **a string or an array of strings**
(confirmed in TikTok's Events API 2.0 parameter reference). Send both ids, as
Meta already does.

### RC9 — smaller normalisation gaps · LOW

- TikTok gets no `state` field.
- TikTok keeps only the Latin variant of Arabic names; Meta gets both.
- `pixel_load_strategy: "immediate"` is stripped from public settings, so it is
  probably dead.
- After a payment redirect the browser Purchase waits for interaction
  (inferred).
- Meta prefers **IPv6 over IPv4** for `client_ip_address`; we send whatever the
  header gives.
- Our `_fbc` omits Meta's optional trailing `.{appendix}` segment
  (`MetaPixel.tsx:126-163`) — harmless, but worth matching the documented
  format.

### RC10 — the client-IP fix creates a spoofable trust boundary · MEDIUM

Once `checkout.py` trusts `X-Forwarded-For`, the first hop is client-controlled
for anyone calling the API directly, and the same value feeds COD fraud scoring
(`checkout.py:3057`).

**Fix.** Trust the forwarded IP only on requests that arrive from the storefront
(shared-secret header or signed hop), and fall back to the connection IP
otherwise. Keep the fraud check on a value resolved under that same rule.

### RC11 — no safety net · MEDIUM

RC1 and RC2 each ran silently for weeks. There is no alert on zero server
Purchases, no canary, no schema-vs-column test, and token failures reach only
Sentry. See §9.

### RC12 — consent does not reach order-based events · MEDIUM

The storefront stores the decision in `localStorage`
(`numu-storefront/src/lib/consent.ts:39`), `<ConsentGate>` withholds browser
pixels when the store sets `consent_required`
(`src/app/[domain]/layout.tsx:385-421`), and a denial sends `opt_out: true` to
`/track` (`src/lib/meta-pixel.ts:436`), which becomes Meta's `opt_out`
(`tasks/meta_capi.py:268`) and TikTok's `limited_data_use`
(`tasks/tiktok_capi.py:226,466`). **The order-based dispatchers carry none of
this**, so a shopper who declined still generates a full server Purchase with
hashed email and phone.

### RC13 — internal traffic filtering is incomplete · MEDIUM

`/track` drops bots and internal traffic before dispatch (`tracking.py:461`),
which is the right place. But `is_internal_traffic`
(`src/application/services/device_classifier.py:163-172`) only catches a hub
referrer (the theme-editor iframe) and the `_npt=` preview parameter. Merchant,
staff and admin-impersonation sessions are not detected, so merchant self-tests
and support sessions reach Meta and TikTok as real shopper events.

---

## 5. Evidence: Vionne's TikTok orders and match-key coverage

Of Vionne's 31 orders since 2026-07-22, 7 came from a TikTok ad (`utm_source=tiktok`
plus a stored `ttclid`), all on or after 2026-09-07. Lifetime across 4 campaigns,
TikTok Ads Manager attributes **4 Purchase events for 3 distinct orders** against
2,582.80 EGP of spend, and all 4 came from the browser pixel.

"Thank-you reached NUMU" means a browser `order_completed` row with a device
value in `funnel_events`, or a thank-you `page_views` row.

| Order | Created (UTC) | Pay | Thank-you reached NUMU | TikTok server Purchase | TikTok Ads attributed | Meta server Purchase | Cause |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ORD-831003 | 09-19 18:18 | COD | No | None | 0 | None | RC1, RC2 |
| ORD-580571 | 09-17 22:19 | COD | No | None | 2 events, 770 EGP total | None | RC1, RC2 |
| ORD-472583 | 09-15 21:43 | COD | No | None | 1 (730 EGP) | None | RC1, RC2 |
| ORD-302721 | 09-14 21:30 | Vodafone Cash | No | None | 0 | Sweep 09-14 22:10, server IP/UA | RC3, RC2, RC4 |
| ORD-744686 | 09-14 21:21 | InstaPay | No | None | 0 | None | Cancelled, replaced by ORD-302721 |
| ORD-604094 | 09-08 12:42 | COD | No | None | 1 (330 EGP) | Sweep 09-14 22:10 (6 days late) | RC1, RC2, RC4 |
| ORD-698791 | 09-07 07:57 | InstaPay | No | Sweep 09-07 08:25, **empty user object** | 0 | Sweep 09-07 09:10 | RC3, pre-`0f48c8f4` sweep |

**Session detail.** For ORD-604094, ORD-472583, ORD-580571 and ORD-831003 the
only `funnel_events` rows are `add_to_cart`, `checkout_started` and
`order_completed`, all emitted server-side. There are zero page views, product
views or collection views, and no `page_views` rows at all. The shoppers
browsed; not one browser-side `/track` request was accepted.

**Control group.** ORD-563588 (iOS Safari) and ORD-582804 (Android Chrome), both
2026-09-13 with no TikTok click, reached NUMU from the thank-you page. Both
platforms received a server Purchase within one minute, carrying email, phone,
name, city, IP, user agent, `_ttp` and full contents. The pipeline works when
there is no long `ttclid`. That isolates RC1.

**Two open anomalies.**

1. TikTok counted 2 Purchases totalling 770 EGP on 09-18, which equals one
   order (ORD-580571). Either TikTok did not merge two browser copies, or one
   carried no value. Check TikTok Events Manager → Purchase → event details for
   2026-09-18.
2. ORD-998780 arrived from Instagram's in-app browser (`fbclid`). Its thank-you
   page view reached NUMU but its browser Purchase did not, so neither platform
   got a server Purchase. This is **not** RC1 — the `fbclid` passed validation.
   It needs a live test in the Instagram in-app browser.

### Match-key coverage today (Vionne, last 14 days, server events)

| Platform | Event | n | IP / UA | Browser id (`_ttp` / `fbp`) | Click id (`ttclid` / `fbc`) | Email / phone |
| --- | --- | --- | --- | --- | --- | --- |
| TikTok | ViewContent | 675 | 100% | 28% | 0% | 0.4% |
| TikTok | AddToCart | 125 | 100% | 97% | 0% | 0% |
| TikTok | InitiateCheckout | 20 | 100% | 95% | 0% | 10% |
| TikTok | AddPaymentInfo | 7 | 100% | 100% | 0% | 100% |
| TikTok | Purchase (since 09-13) | 3 | 100% | 100% | 0% | 100% |
| Meta | PageView | 740 | 100% | 98% | 35% | 2.7% |
| Meta | ViewContent | 675 | 100% | 91% | 10% | 0.4% |
| Meta | AddToCart | 125 | 100% | 100% | 26% | 0% |
| Meta | InitiateCheckout | 20 | 100% | 100% | 30% | 55% / 50% |
| Meta | Purchase (since 08-21) | 20 | 60% (sweep rows carry the server IP) | 70% | 15% | ~100% |

TikTok describes EMQ as a weighted average of match-key coverage, and vendors
weight email and click id highest. With `ttclid` at 0% and email/phone under 1%
on 95% of events, a low score is expected. Meta's last measured EMQ was **6.1**
on all four upper-funnel events (2026-08-17), before the 2026-08-18 fixes, and
has not been re-measured.

---

## 6. Platform rules that constrain the design

Facts below are from official documentation unless marked. `[UNVERIFIED]` means
it could not be confirmed on an official page.

### Meta Conversions API

| Rule | Consequence for NUMU |
| --- | --- |
| Deduplication is `event_id` + `event_name`, 48h window, **first received wins** | Our browser and server legs already share `str(order.id)` and the same `order.total/100` basis — verified. Keep both identical, or a thin browser copy wins over a rich server one |
| `event_time` may be at most **7 days** old, or the whole request errors | A delivered event must be sent inside 7 days. `physical_store` uploads get 62 days |
| Up to 1,000 events per request, and **one invalid event rejects the entire batch** | Our sweep sends 100 per request (`_SEND_BATCH`, `tasks/meta_capi.py:1380`). One poison row can drop 99 good events. Validate per event, and on batch rejection retry individually to isolate it |
| `em`, `ph`, `external_id` accept arrays | Send fingerprint **and** customer id (RC8) |
| `fbc` = `fb.{subdomain_index}.{creation_time}.{fbclid}.{appendix}` | Ours omits the optional appendix (`MetaPixel.tsx:126-163`) |
| **IPv6 is preferred over IPv4** | Prefer the IPv6 form when both are available |
| `action_source` values: `email`, `website`, `app`, `phone_call`, `chat`, `physical_store`, `system_generated`, `business_messaging`, `other` | Merchant-created orders are not `website` |
| `business_messaging` requires `messaging_channel`, `ctwa_clid` and the WABA id, and supports Purchase, OrderCreated, OrderShipped, **OrderDelivered**, OrderCanceled, OrderReturned | The correct path for WhatsApp/DM orders **that came from a click-to-message ad** — NUMU already runs WhatsApp on WABA `991122053507329` |
| Conversion Leads / CRM is only for Meta Lead Ads instant forms (≥200 leads/month) | **Does not fit** website COD orders. Do not plan around it |
| Dataset Quality API: `GET /v25.0/dataset_quality?dataset_id=`, scopes `ads_read` + (`ads_management` \| `business_management`); returns `composite_score`, `match_key_feedback`, `diagnostics`, `acr`, coverage `percentage`, `dedup_key_feedback`, `browser_events_with_dedupe_key`, `server_events_with_dedupe_key`, `upload_frequency` | The concrete basis for Phase 4 |
| Limited Data Use covers 14 US states only; `opt_out: true` means attribution-only | LDU is irrelevant in Egypt; `opt_out` is the lever that matters (RC12) |
| Business Tools Terms require a prominent notice on every page, and verifiable consent where local law requires it | §10 |

### TikTok Events API 2.0

| Rule | Consequence for NUMU |
| --- | --- |
| All match keys live in `data[].user`: `ttclid`, `ttp`, `external_id`, `email`, `phone`, `ip`, `user_agent`. There is no `context` object (that was Events API 1.0) | Confirms our payload shape |
| `email`, `phone`, `external_id` accept **a string or an array**; `ttclid` and `ttp` are string only | RC8 fix is supported |
| **No documented maximum length for `ttclid`** | Our 256 cap is self-inflicted (RC1) |
| TikTok also reads `ttclid` out of `page.url` when present | Cheap second chance: always send the real landing URL in `page.url` |
| Store `ttclid` for **28 days or more** and refresh on each new click | Our 30-day cookie is correct |
| Deduplication is `event_source_id` + `event` + `event_id`, first kept, 48h. A duplicate arriving **within 5 minutes is merged** into the first (e.g. a missing `user.email` is filled in) | Strong argument for sending the server Purchase immediately: within 5 minutes it *enriches* the browser copy instead of being discarded |
| Without `event_id`, TikTok falls back to `_ttp` with a 5-minute window and always drops the server copy | Never omit `event_id` |
| `CompletePayment` → `Purchase` and `SubmitForm` → `Lead` were renamed; old names supported indefinitely. `PlaceAnOrder` and `ClickButton` retire in 2027 | Do not adopt `PlaceAnOrder` |
| **Custom events cannot be optimised on** — reporting and audiences only | A custom "OrderDelivered" on TikTok would be unusable for campaign optimisation. See Phase 3 |
| `event_source`: `web`, `app`, `offline`, `crm`. Offline needs an Offline Event Set, **standard events only**, upload within 28 days, attribution CTA 28d / VTA 7d | The right home for the COD delivered signal on TikTok |
| CRM requires `lead.lead_id` from TikTok Lead Ads instant forms | Does not fit COD |
| Up to 1,000 events per request; more rejects the whole request. Errors name the index of the first invalid event | Same batching caution as Meta |
| **No match-quality (EMQ) API exists.** The closest is `GET /open_api/v1.3/pixel/event/stats/` (attributed / preview / total counts); EMQ is visible only in the Events Manager UI `[UNVERIFIED]` | Phase 4 can automate Meta's EMQ but only counts for TikTok |
| No refund event exists `[UNVERIFIED]` | Refund reversal is Meta-only |

### Safari / WebKit (ITP)

- Script-written storage is deleted after **7 days** without user interaction.
- With link decoration (a landing URL carrying `ttclid`, `fbclid`, …),
  JavaScript-set cookies on that page are capped at **24 hours**.
- Cookies set in an HTTP response by a **third-party CNAME- or IP-cloaked**
  request are capped at 7 days. The IP rule triggers when the response IP shares
  fewer than half the address bits with the main resource (/16 IPv4, /64 IPv6),
  and only when the resolved CNAME is empty.
- When the page and the `Set-Cookie` come from the same origin server, the cap
  is not triggered — inferred from the rule rather than stated. A CDN edge IP
  outside the origin's /16 could still trigger it `[UNVERIFIED]`.
- Safari Private Browsing strips "a subset of query parameters"; WebKit does not
  publish the list, and **`ttclid` is not confirmed** to be on it `[UNVERIFIED]`.
- Safari 26 blocks known fingerprinting scripts from reading query parameters
  and `document.referrer`.

**Consequence.** Our current `_fbc` and `ttclid` cookies are JavaScript-set on a
link-decorated landing page, so on Safari they last **24 hours**, not 90 or 30
days. Server-set cookies from the store's own origin are the fix (Phase 2), and
NUMU sets none today (`src/proxy.ts:393` sets only `numu_locale`).

---

## 7. How Shopify, Zid and Salla do it

**No platform ships a COD-aware delivered or confirmed conversion natively**,
and none documents Purchase timing, match keys, retries or replay. Official
documentation is thin everywhere; `[VENDOR]` and `[UNVERIFIED]` tags mark the
weaker claims.

| Concern | Shopify native | Salla | Zid | NUMU today | NUMU after this plan |
| --- | --- | --- | --- | --- | --- |
| Connect Meta | OAuth app, 3 data-sharing levels (Standard / Enhanced / Maximum) | Paste pixel id; CAPI app with a **manually pasted** access token, Pro/Special plans only | Paste pixel id; CAPI app with a pasted token | OAuth + pixel | Same |
| Connect TikTok | OAuth app, Standard / Enhanced / Maximum | Help article exists, method `[UNVERIFIED]` | TikTok CAPI app in the App Market, details `[UNVERIFIED]` | Pixel + Events API | Same |
| Server Purchase | Enhanced/Maximum send CAPI; **timing disputed** between vendors (thank-you pixel vs order record) `[VENDOR, conflicting]` | Yes, via Salla's CAPI app; timing `[UNVERIFIED]` | Yes, via CAPI apps; timing `[UNVERIFIED]` | Dead for TikTok-ad orders (RC1, RC2) | From the order, every payment method |
| Every order reaches the thank-you page | **Yes** — manual payments show instructions on the order-confirmation page, so Purchase fires at placement, unpaid | `[UNVERIFIED]` | `[UNVERIFIED]` | No (InstaPay, Vodafone Cash) | Not needed — server Purchase comes from the order |
| Pixel / server dedup | Shared `event_id` `[VENDOR]`; community reports of dedup warnings | "The ad platform detects duplicates automatically", no `event_id` spec | Not documented | Same `order.id` and same value basis on both legs — verified | Same |
| Match keys | Name, location, email, phone at Enhanced+; EMQ ≈6.5–7 `[VENDOR]` | "Sent encrypted", no spec | Not documented | Thin pre-checkout; server IP on order events | Full normalised PII + session enrichment |
| Server-set ad cookies (ITP) | **No** — "we do not set advertising cookies through our merchants' storefronts" | No evidence | No evidence | No | Yes (Phase 2) |
| Consent | Customer Privacy API: 4 signals, regional, GPC honoured | Not documented | Not documented | Banner optional; `opt_out` missing on order events (RC12) | On every leg |
| COD delivered / confirmed event | None found | None — left to apps via `order.status.updated` | None — webhook conditions on "delivered" / COD enable apps | None | Native (Phase 3) |
| Refund / cancel to Meta | None found | Order events exist for apps | Webhooks for apps | Dead (RC2) | Yes (Meta only; TikTok has no refund event) |
| Delivery monitoring, retries, replay | None found | None | None | Meta outbox only | Both platforms + alerts + canary + replay |

**What to copy**

- **Shopify: one Purchase per order, at placement, whatever the payment method.**
  NUMU reaches the same end server-side (Phase 1), since our manual rails have no
  confirmation page.
- **Shopify: sandboxed pixels.** App pixels run in a strict sandbox and subscribe
  to standard events, so one broken integration cannot break the others. Keep
  NUMU's per-platform isolation.
- **Salla: a real event layer.** Device Mode (`tracker.js` via app snippet) plus
  Cloud Mode (server-to-server), with `Order Completed` carrying email and phone,
  and order webhooks (`order.status.updated`, `order.refunded`) that third-party
  apps use to build delivered conversions. NUMU's EventBus already plays the
  Cloud Mode role.
- **Zid: webhook conditions.** `order.status.update` can be filtered to fire only
  when status becomes "delivered" or the payment method is COD — exactly the
  Phase 3 trigger.
- **YouCan: a merchant switch for Purchase vs Lead** on COD. Cheap and honest;
  Phase 3 does it properly.

**What to avoid**

- **Pasted access tokens** (Salla, Zid, YouCan) expire silently. NUMU uses OAuth,
  but add the token-expiry alert anyway (G-5).
- **Double Purchase** from a thank-you "event builder" plus the platform pixel,
  which EasyOrders warns about `[VENDOR]`. One `event_id` per order across every
  leg.
- **Browser-dependent server events.** One vendor reports Shopify's native CAPI
  Purchase is dispatched from a web pixel, so a failed thank-you page loses both
  copies `[VENDOR, disputed]`. Our Phase 1 design is explicitly not that.

---

## 8. The plan

Six phases, about 6–7 weeks end to end. Phase 0 stops the loss and is the only
urgent one; each later phase has its own exit criteria and can ship on its own.

| Phase | Goal | Duration | Repos | Exit criteria (summary) |
| --- | --- | --- | --- | --- |
| 0 | Stop the loss: accept full click ids, make every order-based Purchase path work, stop sending the server IP | 1–2 days | api, storefront | TikTok-ad sessions produce browser events; a real `ttclid` reaches TikTok at full length; distinct shopper IPs on new orders |
| 1 | One reliable Purchase for every order, with retries, alerts and a canary | ~1 week | api, storefront, hub | Every order has a server Purchase on both platforms within 5 minutes; failures are visible to the merchant |
| 2 | Identity enrichment: from about 6 to 8–9 match quality | 2–3 weeks | api, storefront | Meta EMQ ≥8.5 on Purchase and checkout events; `_ttp` and click ids survive Safari |
| 3 | A COD signal both platforms can optimise on | 1 week, then tuning | api, hub | Delivered events reach Meta (custom conversion) and TikTok (Offline Event Set) within their windows |
| 4 | Monitoring, so tracking never goes silent again | ~3 days | api, hub | Daily EMQ and coverage trend in the hub; alerts proven on a seeded gap |
| 5 | Later, optional | — | — | Decided per item |

### 8.0 Decisions

Each decision has a default that the plan follows if no one answers by the time
its phase starts.

| # | Decision | Default | Needed by |
| --- | --- | --- | --- |
| D1 | How far to trust the forwarded client IP (RC10). A proper storefront → API shared secret needs one new env var, which standing rules forbid without approval | Order snapshots use the same resolver as `/track` (same risk `/track` already carries). **COD fraud scoring keeps the connection IP** until a shared secret is approved | Phase 0 |
| D2 | When Purchase fires (RC5) | At placement, deduplicated between browser and server, plus a separate delivered signal in Phase 3 | Phase 1 |
| D3 | When manual-rail orders (InstaPay, Vodafone Cash) count as a Purchase | On payment-proof approval, not at creation | Phase 1 |
| D4 | Legal basis for sending hashed email and phone abroad under the PDPL, and whether a transfer licence is needed | Legal review completes before Phase 2 ships; Phase 2 work can be built but stays behind consent | Phase 2 |
| D5 | Which stores move campaign optimisation to the delivered signal | Merchant and ad-ops decide per store once it reaches about 50 delivered orders a week | Phase 3 |
| D6 | Whether to add Snapchat | Revisit after Phase 3, based on merchant demand | Phase 5 |

### Phase 0 — stop the loss (1–2 days)

**0.A Pre-flight checks** (read-only, count-only, owner-approved, same method as §2):

1. Confirm G4 in production: count Purchase rows in `meta_event_log` and
   `tiktok_event_log` created by the payment and courier webhook paths since
   2026-08-01. The expected result is zero, which proves the entity path has
   never sent one.
2. Run the same four queries used for Vionne against **Rabbit**: `ttclid`
   lengths, distinct snapshot IPs and user agents, browser rows in ad-click
   sessions, and server Purchase coverage. This confirms the root causes hold
   for both live stores.

**0.B Build**

| # | Fix | Where | Covers |
| --- | --- | --- | --- |
| 1 | Raise `ttclid` / `gclid` / `fbclid` caps to 2048; stop truncating; add a `mode="before"` validator that nulls an oversized optional field instead of rejecting the event | `tracking.py:397`, `core/entities/attribution.py:27`, `checkout.py:821-825`, storefront `attribution-client.ts:41,109-111` | RC1 |
| 2 | Migration: widen `customer_touches.gclid/fbclid/ttclid` to `Text`, written idempotently, revision id ≤32 chars | new Alembic revision | G1 |
| 3 | `/track` proxy logs every upstream non-2xx with the first error `loc`; the browser still gets 204 | storefront `app/api/storefront/track/route.ts` | RC1 visibility, G-3 |
| 4 | One `order_view()` normaliser called at the start of every shared builder; delete the Meta sweep's hand-built adapter | both purchase dispatchers, `tasks/meta_capi.py:2077` | RC2, G3, G4 |
| 5 | Checkout proxy forwards `X-Forwarded-For`, `CF-Connecting-IP` and `User-Agent`; checkout stops preferring `X-Real-IP` and uses the `/track` resolver for the snapshot; fraud scoring keeps the connection IP (D1) | storefront `app/api/checkout/route.ts`, `checkout.py:790`, `:3057` | RC4, G2, RC10 |
| 6 | Daily alert: a store with orders but zero server Purchase rows, per platform | Celery beat task + `.alert()` log; register the task name **and** its import | G-4 |
| 7 | Schema-vs-column test | api tests | G-1 |
| 8 | Golden payload tests: each builder with a real `OrderModel` and a real `Order` entity; assert full-length `ttclid`, browser UA, non-server IP, phone | api tests | G-2 |

**0.C Ship**

1. **Staging first.** Deploy the API, then the storefront. Land on a staging
   store with a 300-character `ttclid` and an `fbclid`, browse, and place a COD
   test order. Confirm `funnel_events`, `tiktok_event_log` and `meta_event_log`
   rows exist, that the `ttclid` is full length, and that the IP and user agent
   are the browser's.
2. **Production, API before storefront.** A storefront that sends full-length
   click ids to the old API is still rejected. Promote the API with its
   migration, confirm `/track` accepts a 300-character `ttclid`, then promote
   the storefront.
3. **Rollback.** Either repo can be reverted independently. The migration only
   widens columns, so leave it in place on rollback; never downgrade it.
4. **Do not replay past orders.** They are more than 48 hours old, so a resend
   would double-count on Meta.

**Exit criteria**

- The pre-flight results are recorded in this document.
- A real TikTok ad click produces page views, product views and a Purchase in
  NUMU's logs, with the `ttclid` at full length.
- New orders store distinct shopper IPs and browser user agents.
- TikTok sweep rows and webhook Purchase rows reappear in production.
- Zero `/track` 422s in the proxy log, outside deliberate tests.

### Phase 1 — one reliable Purchase for every order (~1 week)

| # | Task | Where | Covers |
| --- | --- | --- | --- |
| 1 | Server Purchase at order creation for COD and card orders, **post-commit** through the EventBus (`OrderCreatedEvent`); `event_id = order.id`; `event_time = order.created_at` (today the dispatchers use `paid_at`, which can fall outside Meta's 7-day window) | new `OrderCreatedEvent` subscriber, both dispatchers | RC3, D2 |
| 2 | Manual-rail Purchase on `PaymentProofApprovedEvent`; confirm approval also stamps `paid_at` (open question 3) | new subscriber; `review_payment_proof.py`, `submit_payment_proof.py` | RC3, D3 |
| 3 | TikTok outbox: generalise `meta_event_log` with a `platform` column; fold `fbtrace_id` / `request_id` into a nullable `vendor_trace_id`; write the row before enqueueing; the sweep skips only rows actually sent | migration + both log repos | RC7 |
| 4 | Send session fingerprint and customer id together as an `external_id` array on both platforms | `tiktok/hashing.py`, Meta hashing | RC8 |
| 5 | Validate each event before batching; on a batch rejection, retry the group one event at a time | `tasks/meta_capi.py` batch sender | §6 batch rule |
| 6 | Persist the consent decision with the order; carry `opt_out` / `limited_data_use` into every order-based event | checkout snapshot + both dispatchers | RC12 |
| 7 | Extend `is_internal_traffic` to merchant, staff and admin-impersonation sessions | `device_classifier.py` | RC13 |
| 8 | Credential failures raise a merchant notification and a "Reconnect" state; the tracking panel shows outbox counts by status and `failure_kind` and the last server Purchase time | `emit_notification()`, `/settings/tracking/*/status`, hub `MetaTrackingPanel` / `TikTokTrackingPanel` | G-5, G-6 |
| 9 | Daily synthetic canary per live store, sending with `test_event_code` | beat task or GitHub Action | G-7 |
| 10 | Admin replay tool for failed and dead-letter rows, within 7 days, keeping the original `event_id` | admin route over the shared outbox | G-8 |
| 11 | Investigate the Instagram in-app browser loss (ORD-998780) with a live test; fix if it is a NUMU defect | storefront thank-you page + `/track` | Open question 2 |
| 12 | Resolve the ORD-580571 double count in TikTok Events Manager | TikTok Events Manager | Open question 1 |

**Exit criteria**

- Every storefront order has a server Purchase on both platforms within 5
  minutes; manual-rail orders within 5 minutes of approval.
- A declined-consent shopper produces no identifiable server event.
- An expired token shows as "Reconnect" in the hub within one day.
- The canary passes daily on Vionne and Rabbit.

### Phase 2 — identity enrichment (2–3 weeks, gated on D4)

| # | Task | Where | Covers |
| --- | --- | --- | --- |
| 1 | First-party visitor cookie set by the server (HTTP-only, long-lived, store origin), linked server-side to any email or phone the shopper gives: WhatsApp OTP, checkout, login, newsletter. Today the OTP-verified phone is keyed by cart session, not the `numu_sid` fingerprint, so build that link | storefront middleware, `storefront/identity.py`, `tracking.py` | RC6 |
| 2 | Set `_fbc`, `_fbp` and `ttclid` with `Set-Cookie` from the store origin so Safari's 24-hour cap does not apply; build `fbc` from the first-seen time in Meta's format, including the appendix; persist `_ttp` server-side once seen | storefront `proxy.ts` / route handlers, `MetaPixel.tsx` | RC6, RC9, §6 Safari |
| 3 | **Load TikTok's script on page view** (when consent allows) instead of after the first interaction, so `_ttp` exists on the first ViewContent | storefront `TikTokPixel.tsx`, `pixel_load_strategy` | RC6 (`_ttp` at 28%) |
| 4 | Widen `_IDENTITY_RESOLUTION_STEPS` to PageView, ViewContent and AddToCart | `tracking.py:102` | RC6 |
| 5 | Finish normalisation: TikTok `state`, Arabic and Latin name variants, IPv6 preference, Meta Parameter Builder rules | both hashing modules | RC9 |
| 6 | Always send the real landing URL as TikTok's `page.url` | `tasks/tiktok_capi.py` | §6 TikTok fallback |

**Exit criteria**

- Meta EMQ ≥8.5 on Purchase and checkout events and ≥7 on browsing events, 14
  days after release (Dataset Quality API).
- TikTok: `_ttp` on ≥90% of ViewContent events; `ttclid` on ≥95% of events from
  ad-click sessions, including Safari.

### Phase 3 — a COD signal both platforms can optimise on (1 week, then tuning)

| # | Task | Where |
| --- | --- | --- |
| 1 | Keep Purchase at placement, deduplicated | — |
| 2 | Meta: a server-only delivered event (custom event plus custom conversion) with its own `event_id` and the real delivery time, sent within 7 days; for orders from click-to-message ads, use `business_messaging` with `OrderDelivered`, `ctwa_clid` and the WABA id | courier webhooks, status handler |
| 3 | TikTok: an **Offline Event Set** with the standard `Purchase` event (TikTok cannot optimise on custom events), uploaded within 28 days | new offline event source per store |
| 4 | Courier webhooks publish status events, so delivery is visible to handlers | `bosta.py`, `jt.py`, `mylerz.py` |
| 5 | Meta Refund for cancelled and returned orders (TikTok has no refund event) | status handler |
| 6 | A per-store switch for which signal campaigns optimise on (D5) | hub tracking settings |

**Exit criteria**

- Delivered events arrive on Meta within 7 days and on TikTok within 28 days of
  delivery, for every delivered order.
- Cancelled and returned orders produce a Meta Refund.

### Phase 4 — monitoring (~3 days)

| # | Task | Where |
| --- | --- | --- |
| 1 | Daily Meta Dataset Quality API pull (`composite_score`, `match_key_feedback`, `dedup_key_feedback`, coverage, `upload_frequency`) | beat task; needs `ads_read` + `ads_management` or `business_management` |
| 2 | TikTok: trend `pixel/event/stats` counts (no EMQ API exists) and link to Events Manager | beat task |
| 3 | Show both trends in the hub tracking panel | hub |
| 4 | Keep the Phase 0 alert, the canary and the proxy rejection counter on permanently; prove each fires on a seeded gap | — |

**Exit criteria:** each alert fires on a seeded failure, and the hub shows 14
days of EMQ and coverage history.

### Phase 5 — later, optional

- **Snapchat Conversions API.** Salla and Zid ship it natively, and Snap is
  large in the Gulf. Worth doing if merchants run Snap ads (D6).
- **Merchant-created orders without an ad click.** Send them to TikTok's Offline
  Event Set and to Meta with the matching `action_source`, only once a merchant
  actually runs ads that close in DMs.
- **Storefront → API shared secret** (D1), if approved, so the forwarded IP can
  also feed COD fraud scoring.

---

## 9. Guardrails

RC1 and RC2 each ran for weeks because failures were swallowed and nothing
counted outcomes. Each guardrail below would have caught at least one within a
day.

| # | Guardrail | Catches | Where | Phase |
| --- | --- | --- | --- | --- |
| G-1 | Schema-vs-column test: every `max_length` on the tracking and attribution schemas is ≤ the database column it lands in | The G1 migration gap | one pytest reading SQLAlchemy column types | 0 |
| G-2 | Golden payload tests, one per Purchase path (thank-you, sweep, status handler, each webhook incl. Fawry, creation, proof approval) × platform, built from a real `OrderModel` through the real load path; assert key **presence**: full-length `ttclid`, browser UA, non-server IP, email/phone | RC2, RC4 | api tests | 0 |
| G-3 | Proxy logs upstream status and the first 422 `loc`; still 204 to the browser | RC1 | `track/route.ts` | 0 |
| G-4 | Daily alert: store had orders but zero server Purchase rows, per platform | RC1, RC2, token expiry | beat task → admin alert | 0 |
| G-5 | `INVALID_CREDENTIALS` rows (Meta 102/190/463/467 + `OAuthException` + 401/403 — subcode 460 arrives under 190; TikTok equivalent at `tiktok_delivery_policy.py:104-118`) raise a merchant notification and a "Reconnect" state in the hub | Silent token expiry | `emit_notification()` + existing tracking panels | 1 |
| G-6 | Hub panel shows outbox counts by `status` and `failure_kind`, last server Purchase time, and per-key coverage | Everything, visibly | `/stores/{id}/settings/tracking/*/status` + hub panels | 1 |
| G-7 | Daily synthetic canary per live store: headless landing with a 400-char `ttclid` and an `fbclid`, browse, place a test order; events sent with `test_event_code` so they never enter real reporting; assert full-length ids, browser UA, and rows in both logs | RC1, RC4, future regressions | beat task or GitHub Action | 1 |
| G-8 | Replay tool: an admin action re-sends failed and dead-letter rows for one store and date range, only within 7 days, keeping the original `event_id` | Outages, after they end | admin route over the shared outbox | 1 |
| G-9 | Daily Dataset Quality API pull and TikTok stats, trended | EMQ regressions | beat task + hub card | 4 |

**What already exists** (so these extend rather than invent): Meta's outbox
lifecycle and delivery policy, per-platform credential classification, expiry
checks against the 48h dedup window and the 7-day max age (`meta_delivery_policy.py:356-363`,
enforced in `_adopt_owned_row`, the batch sender and `expire_overdue`), the
`/track` bot and internal filter ahead of dispatch, and hub panels already
showing `recent_failure_rate` and `recent_event_count`.

---

## 10. Compliance (owner decision)

Egypt's Personal Data Protection Law (151/2020) became enforceable through its
Executive Regulations, issued November 2025 with a one-year compliance window —
so roughly **November 2026**. Law-firm summaries disagree on the decree number
(Ministerial Decree 816/2025 vs MCIT Decision 81/2025); the PDPC's own
publication was not located `[UNVERIFIED]`.

What matters for this plan:

- **Cross-border transfer (Art. 14):** permitted only where the destination's
  protection is not lower than Egypt's **and** the PDPC has licensed or
  authorised it. Art. 15 exceptions require explicit consent plus a listed case.
  Law-firm commentary reports a separate transfer licence costing 50% of the
  controller licence fee `[VENDOR]`. Sending hashed email and phone to Meta and
  TikTok servers abroad is a cross-border transfer of personal data.
- **Direct marketing (Art. 17):** prior consent, sender identity, stated
  purpose and an easy opt-out. Fine EGP 200k–2M (Art. 43).
- **Penalties (Art. 42):** at least 3 months' imprisonment and/or EGP 500k–5M.
- **Hashing is very likely not anonymisation.** Neither the law nor the reviewed
  summaries define anonymisation or pseudonymisation, and personal data covers
  anyone identifiable "directly or indirectly" `[UNVERIFIED]`.
- No cookie- or tracking-specific rule was found in the law text `[UNVERIFIED]`.

**Recommendation.** Do the consent work **before** Phase 2, not after: record a
first-party consent flag, attach it to every event (closing RC12), and get legal
review of the transfer basis before enrichment ships. Retrofitting consent into
identity enrichment is far more expensive than designing it in.

---

## 11. Acceptance criteria

1. Every storefront order has a Meta and a TikTok server Purchase within 5
   minutes — except manual rails, which have one within 5 minutes of proof
   approval.
2. When an order has a `ttclid`, the Purchase carries it **at full length**.
3. Zero `/track` rejections for valid shoppers; the proxy's rejection counter
   stays at zero outside deliberate tests.
4. Real, distinct IPs and browser user agents on every order-based event.
5. A spoofed `X-Forwarded-For` on a direct API call cannot change the stored IP.
6. No event reaches either platform for a shopper who declined consent.
7. **Meta:** EMQ ≥8.5 on Purchase and checkout events, ≥7 on browsing events,
   measured via the Dataset Quality API 14 days after Phase 2.
8. **TikTok:** no EMQ API exists, so the criterion is coverage instead —
   `ttclid` present on ≥95% of events from ad-click sessions, and email or phone
   on ≥95% of Purchase events — with the Events Manager score read manually as a
   cross-check.
9. The canary passes daily on every live store.

---

## 12. Corrections to existing documents

- Auto-memory says the order-based enrichment of the thank-you Purchase is
  "SHIPPED AND LIVE 2026-09-07". It is deployed but has **never run** (RC2).
- The auto-memory entry on the `purchase_trigger` exact match is true but
  secondary: the handler crashes before reaching the exact-match check (RC2), the
  Meta sweep does backfill once `paid_at` is set, and the TikTok sweep is dead.
- `docs/Plans/Vionne/TIKTOK-EVENTS-DIAGNOSTICS-FIX.md` (F6) and
  `docs/Plans/TikTok/TIKTOK-PIXEL-EVENTS-API-DESIGN.md` (§24, the D3 sweep
  handoff) mark items done that do not work in production (RC2).
- `docs/Plans/Meta/META-SIGNAL-QUALITY-OVERVIEW.md` says client-IP forwarding is
  correct. True for `/track` only, not for checkout (RC4, G2).
- `docs/Linkedin/NUMU-Analytics-Features.md`, Parts 1 and 7, say NUMU captures
  every page view and measures sessions per channel. Not true for TikTok-ad
  visitors until RC1 is fixed.

*(These four plan documents were referenced in v1 but are not present in this
repository's working tree; paths are carried over unverified.)*

---

## 13. Open questions

1. Why did TikTok count 2 Purchases for ORD-580571 on 2026-09-18? Check Events
   Manager → Purchase → event details. *(Phase 1, task 12)*
2. Why did ORD-998780's browser Purchase never reach NUMU from Instagram's
   in-app browser, when the thank-you page view did? Needs a live test. *(Phase 1, task 11)*
3. Does `PaymentProofApprovedEvent` also stamp `paid_at`? The Meta orphan sweep
   assumes it does (`tasks/meta_capi.py:68-99`); this was not verified. *(Phase 1, task 2)*
4. Which Vionne campaigns should move to the Phase 3 delivered signal, and at
   what volume? Merchant and ad-ops decision. *(D5, Phase 3)*
5. Legal: is the PDPC transfer licence required before Phase 2 ships? *(D4, before Phase 2)*
6. Does Rabbit show the same root causes as Vionne? *(Phase 0, pre-flight 2)*
7. Has any payment or courier webhook ever produced a server Purchase in production? Expected: no. *(Phase 0, pre-flight 1)*

---

## 14. Sources

**Meta**

- Conversions API — using the API, batching and limits:
  https://developers.facebook.com/documentation/ads-commerce/conversions-api/using-the-api.md
- Server event parameters (`event_time`, `action_source`):
  https://developers.facebook.com/documentation/ads-commerce/conversions-api/parameters/server-event.md
- Customer information parameters (arrays, IPv6, normalisation):
  https://developers.facebook.com/documentation/ads-commerce/conversions-api/parameters/customer-information-parameters.md
- Deduplication:
  https://developers.facebook.com/documentation/ads-commerce/conversions-api/deduplicate-pixel-and-server-events.md
- Parameter Builder library:
  https://developers.facebook.com/documentation/ads-commerce/conversions-api/parameter-builder-library
- Dataset Quality API:
  https://developers.facebook.com/docs/marketing-api/conversions-api/dataset-quality-api/
- Business messaging (`OrderDelivered`, `ctwa_clid`):
  https://developers.facebook.com/documentation/ads-commerce/conversions-api/business-messaging.md
- Conversion Leads:
  https://developers.facebook.com/documentation/ads-commerce/conversions-api/conversion-leads-integration.md
- Data processing options (LDU):
  https://developers.facebook.com/documentation/ads-commerce/marketing-api/overview/data-processing-options.md
- Business Tools Terms:
  https://www.facebook.com/legal/technology_terms

**TikTok**

- Events API 2.0 parameters: https://business-api.tiktok.com/portal/docs?id=1771101151059969
- `ttclid` handling and storage: https://business-api.tiktok.com/portal/docs?id=1771100879787009
- Deduplication: https://business-api.tiktok.com/portal/docs?id=1771100965992450
- Supported standard events: https://business-api.tiktok.com/portal/docs?id=1771101186666498
- Offline events: https://business-api.tiktok.com/portal/docs?id=1771101027431425
- Attribution windows: https://business-api.tiktok.com/portal/docs?id=1771101064635394
- Test events: https://business-api.tiktok.com/portal/docs?id=1771100984456193
- Pixel event stats: https://business-api.tiktok.com/portal/docs?id=1740858904557570
- Standard-event renames: https://ads.tiktok.com/help/article/how-to-adopt-tiktoks-updated-standard-events
- Shopify pixel + Events API results: https://ads.tiktok.com/business/en/blog/shopify-ecommerce-integration-pixel-events-api

**WebKit / Safari**

- Tracking prevention: https://webkit.org/tracking-prevention/
- Third-party IP cookie rule: https://github.com/WebKit/WebKit/pull/5347
- Private Browsing 2.0: https://webkit.org/blog/15697/private-browsing-2-0/
- Safari 26 features: https://webkit.org/blog/17333/webkit-features-in-safari-26-0/

**Shopify**

- Meta data sharing levels: https://help.shopify.com/en/manual/promoting-marketing/analyze-marketing/meta-data-sharing
- TikTok data sharing: https://ads.tiktok.com/help/article/data-sharing-tiktok-app-shopify
- Web Pixels API: https://shopify.dev/docs/api/web-pixels-api
- `checkout_completed`: https://shopify.dev/docs/api/web-pixels-api/standard-events/checkout_completed
- Manual payments: https://help.shopify.com/en/manual/payments/manual-payments
- Customer Privacy API: https://shopify.dev/docs/api/customer-privacy
- Pixel privacy: https://shopify.dev/docs/api/web-pixels-api/pixel-privacy
- Cookies: https://www.shopify.com/legal/cookies
- Server pixels (limited release): https://shopify.dev/changelog/server-pixels-limited-release

**Zid / Salla / MENA**

- Salla Meta CAPI app: https://help.salla.sa/article/741081803
- Salla e-commerce events: https://docs.salla.dev/1804461m0 · https://docs.salla.dev/1724365m0
- Salla order webhooks: https://docs.salla.dev/1894252m0
- Zid Facebook pixel: https://help.zid.sa/en/facebook-pixel
- Zid app scripts: https://docs.zid.sa/app-scripts-649611m0
- Zid webhooks (delivered / COD conditions): https://docs.zid.sa/webhooks
- YouCan Meta CAPI (Purchase vs Lead): https://youcan.shop/en/help/application/facebook-conversion-api
- EasyOrders duplicate-pixel warning: https://www.easyorders.eg/blog/pixel-duplicate-events/
- COD conversion practice: https://www.maxmegroup.com/learn/track-cash-on-delivery-orders-meta-capi

**Vendor benchmarks** (all `[VENDOR]`)

- Elevar: https://docs.getelevar.com/docs/how-to-get-the-most-value-out-of-elevar
- Elevar session enrichment: https://docs.getelevar.com/docs/session-enrichment-user-identity-tracking-overview
- Littledata: https://help.littledata.io/integrations/facebook-capi/how-it-works-shopify-to-meta-conversions-api
- Stape Cookie Keeper: https://stape.io/helpdesk/documentation/cookie-keeper-power-up
- Stape on Safari ITP: https://stape.io/blog/safari-itp
- TrackBee: https://www.trackbee.ai/blog/how-to-improve-metas-event-match-quality-score-for-better-ad-performance-with-trackbee

**Egypt PDPL**

- Law 151/2020 (unofficial English): https://eg.andersen.com/wp-content/uploads/2025/06/Law-No.-151-OF-2020.pdf
- Executive Regulations commentary: https://www.glaco.com/blog/a-first-look-at-egypts-personal-data-protection-executive-regulations/
- Timeline commentary: https://www.lexismiddleeast.com/eJournal/2026-01-14_35/en
