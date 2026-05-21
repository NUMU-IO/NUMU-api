# Feature Specification: UTM & Campaign Attribution Tracking

**Feature Branch**: `001-utm-campaign-attribution`
**Created**: 2026-05-21
**Status**: Draft
**Input**: User description: "Full UTM and campaign attribution tracking across NUMU (backend + storefront + merchant hub). Close the loop between MarketingCampaigns (broadcast vehicles today) and Orders (partial UTM capture today) so merchants can create a campaign, generate trackable links to products/collections/storefront, send them via any channel, and see attributed sessions/add-to-carts/orders/revenue per campaign. UTMs must persist across the visitor journey and tag every funnel event, not just the final order."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Trackable Campaign Links with Order Attribution (Priority: P1)

A merchant running a paid Facebook ad for an Eid sale needs to know which orders came from that ad. They create a marketing campaign in the dashboard, generate a trackable link pointing to a specific product (or the storefront homepage), paste it into their Facebook ad. When a shopper clicks the ad, lands on the storefront, and completes a purchase, the order is unambiguously attributed to that campaign. The merchant can open the campaign and see attributed orders and revenue.

**Why this priority**: This is the minimum viable slice. Without it, "campaigns" remain a send log with no link to revenue, which is the core business problem. Every other user story builds on the data captured here.

**Independent Test**: Create a campaign, generate a trackable link, complete one purchase using that link, open the campaign — the order is listed and contributes to attributed revenue. Fully demonstrable end-to-end with one buyer.

**Acceptance Scenarios**:

1. **Given** a merchant has an active marketing campaign called "Eid Sale 2026", **When** they open the campaign and request a trackable link to product /abc with source "Facebook", **Then** the system produces a URL containing the campaign's stable identifier and the chosen source, ready to copy or share.
2. **Given** a shopper clicks a trackable campaign link and reaches a product page, **When** they complete a checkout without navigating away from the campaign-tagged URL, **Then** the resulting order is linked to the originating campaign and visible in that campaign's order list.
3. **Given** an attributed order exists, **When** the merchant opens the campaign's performance view, **Then** they see at minimum: number of attributed orders, attributed revenue, and the average order value for those orders.
4. **Given** a merchant requests a trackable link for a destination that does not exist (deleted product, invalid collection), **When** the system attempts to build the URL, **Then** the merchant sees a clear error and no broken link is generated.

---

### User Story 2 - Attribution Persists Across the Visitor Journey (Priority: P2)

A shopper clicks a campaign link, lands on the storefront, browses for 15 minutes (homepage → collection → 3 products → cart → checkout), and only then completes the purchase. The campaign attribution survives the entire journey even though the campaign-tagged URL only appears in the address bar on the first page. Without this, P1 only attributes orders from buyers who purchase from the exact landing page without navigating — most real buyers are lost.

**Why this priority**: P1 captures the easy case (direct-purchase clickthrough). P2 captures the realistic case (browsing before buying). Without P2, attribution numbers will be a tiny fraction of true campaign-driven revenue and merchants will lose trust in the data.

**Independent Test**: Click a campaign link, navigate to at least 3 other pages within the storefront, then check out. The completed order is attributed to the originating campaign.

**Acceptance Scenarios**:

1. **Given** a shopper lands on the storefront via a campaign-tagged URL, **When** they navigate to other pages without those tags in the URL, **Then** subsequent page activity continues to be associated with the originating campaign throughout the visit.
2. **Given** a shopper returns to the storefront within the attribution window without a new campaign-tagged URL, **When** they complete a purchase, **Then** the order is still attributed to the most recent campaign they arrived through.
3. **Given** a shopper arrives via campaign A, browses, then later arrives via campaign B and purchases, **When** the order is recorded, **Then** the order is attributed to campaign B (last-touch wins) and campaign A is preserved separately as the first-touch source for that customer.
4. **Given** a shopper has declined marketing cookies in the consent banner, **When** they arrive via a campaign-tagged URL, **Then** attribution behaves as defined under FR-009 (see Clarifications).
5. **Given** the attribution window has expired since a shopper's last campaign visit, **When** they return organically and purchase, **Then** the order is recorded as having no campaign attribution rather than reusing stale data.

---

### User Story 3 - Funnel Performance per Campaign (Priority: P3)

A merchant needs to know not just which campaigns drove orders, but where in the funnel each campaign loses people. Campaign A might drive 1000 sessions but only 5 orders (broken landing-page experience); campaign B might drive 200 sessions and 50 orders (qualified traffic). Without funnel attribution, both campaigns look the same in the order count and the merchant can't fix what's broken.

**Why this priority**: This converts attribution from a vanity metric (orders per campaign) into a diagnostic tool (where each campaign breaks down). Builds on P2 — once attribution survives the journey, every step of that journey can be tagged.

**Independent Test**: Run two campaigns with different audience quality. Confirm the dashboard shows session counts, add-to-cart counts, checkout-started counts, and order counts for each, with conversion percentages between each step.

**Acceptance Scenarios**:

1. **Given** shoppers arrive via a campaign and trigger funnel events (page view, product view, add to cart, checkout started, order completed), **When** the merchant opens the campaign performance view, **Then** they see counts for each funnel stage attributed to that campaign.
2. **Given** a campaign has at least one of each funnel event, **When** the merchant views performance, **Then** conversion percentages between adjacent funnel stages are displayed.
3. **Given** the merchant filters a campaign's performance by date range, **When** they apply the filter, **Then** all funnel counts, conversion percentages, and revenue update to match that date range.
4. **Given** a campaign has zero attributed sessions, **When** the merchant views performance, **Then** the view shows an empty state with guidance rather than a broken or misleading number.

---

### User Story 4 - Organic Customer Share Attribution (Priority: P4)

A delighted customer taps "Share to WhatsApp" on a product page and sends the link to a friend. The friend purchases. The merchant can see that this sale came through a customer share (rather than counting it as untracked direct traffic) and can quantify the value of word-of-mouth virality coming from their product pages.

**Why this priority**: Lower priority than paid-campaign attribution because organic shares are usually a smaller percentage of revenue and merchants prioritize understanding paid spend first. But it costs little to add once the attribution machinery exists.

**Independent Test**: Tap a share button on a product page, send the link to a second device, open it and complete a purchase. The order is recorded as a customer-share-attributed order, distinguishable from both paid-campaign and untracked direct orders.

**Acceptance Scenarios**:

1. **Given** a shopper is on a product detail page, **When** they tap a share button (WhatsApp, Facebook, Instagram, copy-link, etc.), **Then** the outgoing URL carries tags identifying the share as a customer-originated share via that channel.
2. **Given** a friend clicks a customer-shared link and completes a purchase, **When** the merchant views their traffic-source breakdown, **Then** customer-share orders are reported as a distinct source, separable from paid campaigns and direct traffic.
3. **Given** a customer shares a link, **When** the recipient lands on the storefront, **Then** the recipient does not see the sharer's identity (no privacy leak between shoppers).

---

### Edge Cases

- **Stale or unknown campaign codes**: A shopper arrives with a `utm_campaign` value that does not correspond to any existing campaign on this store (campaign deleted, typo in a hand-edited URL, copy-pasted from a different store). System behavior is defined in FR-011 (see Clarifications).
- **URL parameter tampering**: A shopper edits the URL to inject malicious or oversized strings into UTM parameters. System sanitizes and length-caps all UTM values before storage.
- **Returning customer with stored attribution from prior session**: A repeat customer's prior `first_touch` is preserved (it represents how they originally discovered the store); their `last_touch` reflects the most recent campaign click. The order is attributed to last-touch.
- **Direct traffic (no UTMs at all)**: The order is recorded with empty attribution fields and no campaign link. It still appears in the traffic-sources report under "direct" but does not pollute any campaign's performance numbers.
- **Visitor with declined marketing consent**: Per FR-009, first-party attribution is treated as functional analytics and persists regardless of the marketing-cookie decision. The visitor's choice still governs whether their data is shared with third-party ad platforms (Meta CAPI etc.); it does not govern whether the merchant knows which campaign brought the visitor in.
- **Consent revoked mid-session**: Same as above — attribution data continues to persist because it is classified as functional. Third-party fan-out is paused (or scrubbed of identifiers, per each integration's own consent contract).
- **Campaign deleted after orders attributed**: Attributed orders retain their campaign linkage (historical accuracy) even if the campaign record is soft-deleted; the campaign dashboard still loads in read-only mode.
- **Multiple device journey**: Shopper clicks campaign on phone, completes purchase on laptop. This is out of scope (cross-device stitching is a non-goal); the laptop purchase is recorded as direct or whatever its own browsing context indicates.
- **Trackable link to a now-out-of-stock product**: The link still resolves and the page still loads (showing "out of stock"); attribution is still captured if the shopper later purchases a different product in the same visit.
- **Bot/crawler traffic**: Out of scope per non-goals; bot hits are recorded the same as human hits in v1, accepting that campaign numbers will include some bot inflation until a later release adds filtering.

## Requirements *(mandatory)*

### Functional Requirements

#### Trackable Link Generation
- **FR-001**: Merchants MUST be able to generate a trackable link from inside any active marketing campaign in their dashboard.
- **FR-002**: The link generator MUST allow the merchant to choose a destination either (a) by picking from typeahead-searchable lists for the common destinations — storefront homepage, collection page, specific product page — or (b) by entering a custom path on the same store's storefront.
- **FR-002a**: When the merchant supplies a custom destination path, the system MUST validate that the path resolves on this store's storefront before producing the trackable link; an invalid path MUST surface a clear error and MUST NOT produce a broken link.
- **FR-003**: The link generator MUST allow the merchant to choose a traffic source from a preset list (Facebook, Instagram, WhatsApp, Email, TikTok, SMS, QR, Other) and MUST allow editing the medium, term, and content tags before the link is produced.
- **FR-004**: Each generated link MUST include a stable identifier that resolves unambiguously to the originating campaign even if the merchant later renames the campaign.
- **FR-005**: The link generator MUST output the trackable URL ready for copy-paste and MUST also offer a downloadable QR code image for offline use.
- **FR-006**: The system MUST produce trackable URLs that resolve correctly for stores on subdomain hosting and for stores using a custom domain.

#### Attribution Capture & Persistence
- **FR-007**: When a shopper arrives on the storefront with any UTM-style parameters in the URL, the system MUST capture all five standard UTM dimensions (source, medium, campaign, term, content) plus the referring URL and the landing page.
- **FR-008**: The system MUST persist captured attribution across the shopper's visit so that subsequent page views, add-to-cart events, checkout starts, and the final order all remain associated with the campaign that brought the shopper in, even after the URL no longer contains the original tags.
- **FR-009**: The system MUST treat first-party attribution data as functional analytics required to operate the platform for merchants — not as marketing tracking — and MUST therefore persist attribution for every visitor regardless of the marketing-cookie consent decision. Third-party ad-platform integrations that share visitor data outside the platform (e.g., the existing Meta CAPI fan-out and any future TikTok/Snap/Google Ads pixels) remain consent-gated under their own controls and are not affected by this requirement. The cookie banner copy and category descriptions MUST be updated so the platform's behavior matches what the visitor is told.
- **FR-010**: The system MUST distinguish first-touch (how a shopper originally arrived) from last-touch (how they arrived for the current purchase) and MUST persist both for every order.
- **FR-011**: When a shopper arrives with a `utm_campaign` value that does not match any existing campaign on this store, the system MUST record the order and any funnel events with the raw UTM strings preserved but with no resolved campaign reference. Such traffic MUST still appear in the existing traffic-sources analytics keyed by raw `utm_source` / `utm_campaign` strings, but MUST NOT create campaign records implicitly and MUST NOT appear in any specific campaign's performance view.
- **FR-012**: All UTM and attribution values arriving from the storefront MUST be sanitized (control characters stripped, length-capped to a safe maximum) before being persisted to prevent injection or storage abuse.

#### Order, Funnel, and Customer Attribution Linkage
- **FR-013**: Every order MUST record both the raw UTM values that produced it and a resolved campaign reference when one exists.
- **FR-014**: Every funnel event (page view, product view, add to cart, checkout started, order completed) recorded during an attributed visit MUST carry the same UTM and campaign fields so funnel reporting can be filtered or grouped by campaign.
- **FR-015**: Each customer record MUST capture the campaign and source that produced their first-ever attributed order, preserved separately from subsequent purchase attributions, to enable customer-lifetime-value-by-acquisition-channel analysis in future work.
- **FR-016**: When a shopper uses a product-page share button, the outgoing URL MUST carry tags identifying it as a customer-originated share and the social channel used; downstream visits and orders from those URLs MUST be attributable as "customer share" traffic.

#### Reporting
- **FR-017**: Each campaign's performance view MUST display attributed session count, add-to-cart count, checkout-started count, attributed order count, attributed revenue, and average order value.
- **FR-018**: The campaign performance view MUST display conversion percentages between adjacent funnel stages (sessions → ATC, ATC → checkout, checkout → order).
- **FR-019**: The campaign performance view MUST support filtering by an arbitrary date range; all metrics MUST recompute to match the filtered range.
- **FR-020**: The campaign performance view MUST list the top products purchased through attributed orders for that campaign.
- **FR-021**: The orders list in the merchant dashboard MUST visibly indicate which campaign (if any) drove each order, so merchants can see attribution at a glance without opening a campaign.

### Key Entities

- **MarketingCampaign**: Existing entity (broadcast vehicle: email/WhatsApp/SMS). Extended with a stable short identifier used in trackable links so renames don't break attribution. Each campaign gains a performance view that aggregates attributed sessions, add-to-carts, checkouts, orders, and revenue.
- **TrackableLink** (conceptual, not necessarily a persisted entity): A URL produced by the link generator. Carries the campaign's stable identifier and the merchant's chosen source/medium/term/content tags. Resolves to a destination (storefront homepage, collection page, or specific product page).
- **AttributionRecord** (stored on Order and on each FunnelEvent, plus carried in the visitor cookie): The full set of UTM dimensions plus first-touch snapshot, last-touch snapshot, referring URL, and landing page URL. Distinguishes "how this visitor originally arrived" from "how they arrived for this purchase".
- **Order**: Extended to record full attribution (both UTM raw values and the resolved campaign reference) and to surface campaign attribution in the order list.
- **FunnelEvent**: Existing entity capturing page view, product view, add to cart, checkout started, order completed. Extended so each event carries the visitor's current attribution, enabling per-campaign funnel reporting.
- **Customer**: Existing entity. Extended to remember the very first campaign and source that produced an attributed order from this customer, separately from any subsequent purchase's attribution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A merchant can produce a working trackable link from an existing campaign in under 30 seconds (open campaign → choose destination → choose source → copy link).
- **SC-002**: At least 95% of orders placed by shoppers who arrived via a trackable campaign link within the attribution window are correctly attributed to that campaign — measured against a deterministic test suite that simulates campaign-click → browse → purchase journeys.
- **SC-003**: A shopper who lands via a campaign link, navigates at least 3 additional pages within 20 minutes, and then purchases has their order attributed to the originating campaign 100% of the time (when marketing-cookie consent is granted).
- **SC-004**: After running a campaign for 7 days, a merchant can view sessions, add-to-carts, checkouts, orders, revenue, AOV, and conversion percentages for that campaign without leaving the dashboard.
- **SC-005**: Attributed-order share of total orders is reportable per campaign within 5 minutes of an order being placed (no day-long batch lag).
- **SC-006**: Zero shoppers report broken pages from clicking trackable links — verified by automated end-to-end tests covering subdomain hosting, custom-domain hosting, deleted products, and unknown campaign codes.
- **SC-007**: Storefront page load time on a campaign-tagged landing URL is not measurably slower (more than 50 ms) than the same page loaded without attribution, on a representative connection.
- **SC-008**: Campaign-performance dashboard renders complete data for a campaign with up to 10,000 attributed funnel events in under 3 seconds.
- **SC-009**: Within 30 days of release, at least 60% of stores with at least one active marketing campaign have generated at least one trackable link — indicating the feature is discoverable and usable.

## Assumptions

- **Last-touch attribution for v1**: When a shopper arrives via multiple campaigns within the attribution window, the order is attributed to the most recent campaign (last-touch). First-touch is preserved separately on the order and on the customer record so multi-touch models can be added later without backfilling data. This matches the user's explicit non-goal that multi-touch attribution is out of scope for v1.
- **90-day attribution window**: Persistent attribution survives 90 days of shopper inactivity before being considered stale. This matches common industry defaults (Google Analytics, Meta Ads) and balances campaign-cycle length against attribution dilution. Configurable later if merchants want shorter windows.
- **Single-device only**: A shopper who starts on phone and finishes on laptop is treated as two separate sessions; the laptop order is attributed to whatever brought the laptop session in (likely direct). Cross-device stitching is an explicit non-goal.
- **Bot traffic is recorded as-is**: Per the explicit non-goal, no bot/crawler filtering in v1. Merchants will see some bot inflation in session counts. A future release can add filtering retroactively.
- **No new ad-platform integrations**: Meta CAPI already exists and continues to fire as it does today. TikTok, Snap, Google Ads pixels are not added in this feature.
- **No short-link service in v1**: Trackable links are full URLs with all UTM parameters visible. A branded short-link redirector (e.g., `numueg.app/r/xyz`) is a future enhancement.
- **Coupons remain independent of campaigns in v1**: A campaign cannot yet auto-issue or count unique coupon redemptions. Merchants can still hand-attach a coupon code to a trackable link via the `utm_term` or `utm_content` parameter for manual reporting. The Coupon ↔ Campaign foreign key is deferred to v2.
- **Existing infrastructure is reused**: The existing cookie-consent banner, funnel event capture, traffic-sources analytics endpoint, and Meta CAPI fan-out continue to operate without rework; this feature extends them, it does not replace them.
- **Custom-domain SSL is already handled**: The existing canonical-origin logic already routes custom domains correctly; trackable links inherit that routing without new SSL or DNS work.
- **Privacy regime**: Egyptian e-commerce context with no GDPR / CCPA enforcement applied. First-party attribution is classified as functional analytics and persists for all visitors (see FR-009). The existing cookie banner continues to govern third-party ad-platform fan-out (Meta CAPI today). If the merchant base later expands to EU or California buyers, FR-009 must be revisited.

## Clarifications

Three scope-impacting questions were resolved during specification. Each is now reflected in the corresponding functional requirement; this section preserves the decisions and rationale.

1. **Declined-consent visitor behavior** (FR-009) — **Resolved: attribution always persists; classified as functional analytics, not marketing.** First-party attribution (merchant knows which campaign brought a visitor in) fires for every visitor. Third-party ad-platform fan-out (Meta CAPI etc.) remains separately consent-gated under each integration's own controls. The cookie-banner copy will be updated so what visitors are told matches what the platform does. Rejected alternatives: session-only attribution (undercounts campaign numbers in ways merchants distrust), no attribution at all under decline (same problem worse). **Assumes Egyptian / non-GDPR audience**; if the platform later serves EU or California buyers, this requirement must be revisited.

2. **Unknown utm_campaign handling** (FR-011) — **Resolved: null campaign reference; raw UTM strings still recorded.** Keeps the campaign list merchant-curated. External UTM-tagged traffic remains visible in the existing traffic-sources report by raw string, so no data is lost — it just doesn't pollute the campaign performance views. Rejected alternative: auto-creating "external" campaign records (vulnerable to bot/typo pollution, e.g., a script-kiddie hitting `?utm_campaign=PWN3D` would create a permanent campaign entry).

3. **Trackable-link destination scope** (FR-002 / FR-002a) — **Resolved: typeahead pickers for homepage, collection, product + a custom-path escape hatch with server-side validation.** Covers the common case fast and accommodates landing-page or custom-theme-route campaigns without producing broken links. Rejected alternatives: picker-only (excludes legitimate landing-page campaigns), freeform-only (high typo risk producing broken links).
