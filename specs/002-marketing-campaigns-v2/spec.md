# Feature Specification: Marketing Campaigns v2 — Shopify-style rebuild + NUMU enhancements

**Feature Branch**: `002-marketing-campaigns-v2`
**Created**: 2026-05-24
**Status**: Draft
**Input**: User description: "Shopify-style Marketing Campaigns rebuild — UX parity with Shopify Marketing > Campaigns, plus 4 NUMU-exclusive enhancements."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Marketing nav restructure (Priority: P1)

A merchant opens the hub and looks for campaign tools. Today, "Campaigns" sits alongside "Email Templates" as a top-level sibling, while related surfaces like LTV and Multi-touch attribution are buried two clicks deep under Analytics. The merchant wants a single "Marketing" parent in the sidebar that groups Campaigns and Attribution as sub-items, so their marketing surfaces are co-located.

**Why this priority**: Nav is the entry point. Without grouping, merchants miss the Attribution surfaces (LTV, multi-touch) entirely. Cheapest change, highest discoverability lift, and a prerequisite for US2.

**Independent Test**: After deploying only US1, a merchant on a fresh login sees a collapsible "Marketing" parent in the sidebar. Expanding it reveals "Campaigns" and "Attribution" sub-items. Clicking each navigates correctly. Email Templates and WhatsApp remain as top-level items. RTL (Arabic) layout works.

**Acceptance Scenarios**:

1. **Given** a merchant on the hub, **When** they look at the sidebar, **Then** they see a "Marketing" parent group with sub-items "Campaigns" and "Attribution"
2. **Given** the merchant clicks "Marketing", **When** the group is collapsed, **Then** it expands; if expanded, it collapses
3. **Given** the merchant clicks "Campaigns", **When** they're routed, **Then** they land on the campaigns list at `/campaigns`
4. **Given** the merchant clicks "Attribution", **When** they're routed, **Then** they land on the consolidated Attribution page at `/marketing/attribution`
5. **Given** the merchant is viewing the hub in Arabic, **When** they see the Marketing group, **Then** labels and ordering respect RTL and the group title reads "التسويق"

---

### User Story 2 - Consolidated Attribution page (Priority: P1)

A merchant who wants to compare LTV-by-channel against multi-touch attribution today must navigate to two separate pages under Analytics, each with its own date range picker. They want one Attribution page with both views as tabs under a single shared date range, so they can flip between perspectives without losing context.

**Why this priority**: Without it, US1's "Attribution" link has nowhere meaningful to go. Also addresses the most-cited friction in merchant feedback (re-setting date ranges across attribution dashboards).

**Independent Test**: With only US1 + US2 deployed, the merchant navigates to `/marketing/attribution`. They see a date range picker at the top, then two tabs: "LTV by channel" and "Multi-touch". Changing the date range updates both tabs. Clicking the old `/analytics/ltv` URL redirects to the LTV tab on the new page. Same for `/analytics/multi-touch` → Multi-touch tab.

**Acceptance Scenarios**:

1. **Given** the merchant navigates to `/marketing/attribution`, **When** the page loads, **Then** they see a date range picker, two tabs ("LTV" and "Multi-touch"), and the LTV tab is selected by default
2. **Given** the merchant changes the date range, **When** they switch tabs, **Then** the same date range is preserved
3. **Given** the merchant opens `/analytics/ltv` or `/analytics/multi-touch` from a bookmark, **When** the route resolves, **Then** they're redirected to the corresponding tab on the new Attribution page
4. **Given** the Multi-touch tab is selected, **When** the merchant picks an attribution model (last_touch / first_touch / linear / time_decay / position_based), **Then** the panel updates without a full page reload

---

### User Story 3 - Shopify-style Campaign Detail page (Priority: P1)

A merchant clicks into a campaign from the list and wants to see the same information density and layout pattern as Shopify's Create-campaign view: KPI cards at the top, chart breakdowns by channel / UTM / device / customer cohort below, and a permanent right-sidebar containing campaign metadata + shareable links (instead of the current tab-gated TrackableLinkBuilder). Today's tabbed view (Overview / Trackable Links / Discount Codes / Performance / Audience disabled) makes the merchant click to find each piece.

**Why this priority**: This is the headline of the feature. Without it, the rebuild is invisible. P1 because every subsequent UX-affecting story (auto-match rules panel, AI tips, activities) docks INTO this layout.

**Independent Test**: With US1 + US2 + US3 deployed, the merchant opens a campaign with attributed traffic. They see: a sticky header with breadcrumb, campaign title, status badge, date range pill, attribution model pill, and action buttons (Send Now / Schedule / Cancel). A 320px right-sidebar shows the campaign name (inline editable), short_code with copy button, and a permanent Shareable Links section. The main area shows 4 KPI cards (Sessions / Sales / Orders / AOV) in a row, then a 2-column grid of 8 chart panels (Sessions by channel, Sales by channel, Sessions by UTM, Sales by UTM, Orders new-vs-returning, Sales by order size, Items sold by product, Sessions by device). All panels show real data; empty windows show "No data for this date range." not stubs.

**Acceptance Scenarios**:

1. **Given** the merchant opens a campaign detail page, **When** the page loads, **Then** they see the Shopify-style layout: sticky header, right sidebar, KPI cards row, then 2-column chart grid
2. **Given** the page is showing a campaign with attributed traffic, **When** they read the KPI cards, **Then** Sessions / Sales / Orders / Average order value reflect the current date range
3. **Given** the merchant changes the date range pill, **When** the range updates, **Then** all 4 KPI cards and 8 chart panels refresh
4. **Given** the merchant changes the attribution model pill, **When** the model changes, **Then** the KPI cards and panels recalculate using the new model
5. **Given** a chart panel has no data in the selected window, **When** it renders, **Then** it shows the empty-state copy "No data for this date range." not a blank box
6. **Given** the merchant wants to generate a trackable link, **When** they look at the right sidebar, **Then** the Shareable Links section is permanently visible (no tab gating); clicking "+ Add link" opens the builder
7. **Given** the merchant inline-edits the campaign name in the right sidebar, **When** they tab away, **Then** the change is persisted and the breadcrumb updates
8. **Given** the merchant is on a small screen (< 1024px), **When** the page renders, **Then** the right sidebar collapses to a drawer toggle and the chart grid becomes 1-column

---

### User Story 4 - Auto-match rules (Priority: P2)

A merchant runs ads on a partner network that doesn't let them set per-link UTMs with their campaign's `short_code`. They want to define a rule: "any traffic where `utm_source=partner-x` AND `utm_medium=cpc` should be attributed to this campaign". The system applies the rule at funnel-event ingestion, stamping `campaign_id` on matching rows before the existing short_code-based lookup.

**Why this priority**: Eliminates a real friction point (partner networks that won't accept custom UTM patterns), but the campaign still works without it as long as short_codes are used. Not a blocker for the rebuild — P2.

**Independent Test**: With auto-match rules deployed on top of the P1 stack, a merchant creates a rule on Campaign A: `field=utm_source, operator=equals, value=partner-x`. They send a synthetic funnel event from the storefront with `?utm_source=partner-x`. The resulting funnel event row has `campaign_id` set to Campaign A's id, without a `short_code` ever appearing in the URL. Deleting the rule stops new traffic from being auto-attributed.

**Acceptance Scenarios**:

1. **Given** the merchant is on a campaign's right-sidebar Auto-match rules panel, **When** they click "+ Add rule", **Then** an editor modal opens with fields for field / operator / value and an "AND/OR" toggle for combining rules
2. **Given** the merchant saves a rule, **When** an incoming funnel event matches it, **Then** the funnel event's `campaign_id` is set to this campaign
3. **Given** a funnel event would match multiple campaigns' rules, **When** ingestion runs, **Then** the rule with the highest priority order wins (first-match-wins, store-scoped global precedence)
4. **Given** a funnel event arrives with a valid `short_code`-bearing URL AND it would also match an auto-match rule, **When** ingestion runs, **Then** the short_code takes precedence (explicit beats implicit)
5. **Given** the merchant deletes a rule, **When** new funnel events arrive that would have matched, **Then** they are NOT auto-attributed (rules apply only to new traffic, not historical events — historical attribution uses US5)

---

### User Story 5 - Manual attribution backfill (Campaign activities) (Priority: P2)

A merchant created a campaign in NUMU after running an external blast for a week without UTM tagging. They want to retroactively attribute that historical traffic to the new campaign so their analytics reflect the full picture. They open the campaign's "Campaign activities" panel, click "Run backfill", define a filter (e.g., "`utm_source = instagram` AND `referrer LIKE %t.co/%`" within a date window), and the system updates `orders.campaign_id` + `funnel_events.campaign_id` for past rows that match.

**Why this priority**: A genuine need (forgotten UTM tagging on past sends is a common merchant pain), but workable without it (LTV/multi-touch still works on attributed data going forward). P2.

**Independent Test**: With US5 on top of P1/US4, a merchant clicks "Run backfill" on a campaign and submits filters. The system reports the affected_count (e.g., "Backfilled 47 orders and 312 funnel events"). Querying `orders.campaign_id` for the backfilled IDs returns this campaign. The campaign's KPI cards include the backfilled rows on the next render. A backfill entry appears in the activities log with timestamp + filter snapshot + affected_count.

**Acceptance Scenarios**:

1. **Given** the merchant is on the Campaign activities panel, **When** they click "Run backfill", **Then** a modal opens with filter editor (utm field selectors + date range)
2. **Given** they submit valid filters, **When** the backfill runs, **Then** the system updates matching `orders.campaign_id` and `funnel_events.campaign_id` within the date range
3. **Given** the backfill completes, **When** the activities log refreshes, **Then** a new entry shows timestamp, filter snapshot, and affected_count
4. **Given** a row's `campaign_id` is already set to a different campaign, **When** a backfill would re-attribute it, **Then** it's skipped (no silent overwrite)
5. **Given** the merchant runs the same backfill twice, **When** the second run executes, **Then** it idempotently reports 0 affected (because already-attributed rows are skipped)

---

### User Story 6 - One-click duplicate (Priority: P3)

A merchant runs a monthly "Newsletter" campaign that's identical month-to-month except for the body. They want to duplicate last month's campaign with one click, get a new Draft with the same channel / audience / subject, edit the body, and send.

**Why this priority**: Genuine quality-of-life improvement but easily worked around by manually re-creating. P3.

**Independent Test**: With duplicate deployed, the merchant clicks "Duplicate" on a campaign's list-row hover or detail-page header. A new Draft campaign appears with name appended " (Copy)", same channel / subject / body / audience_filter / segment_id. A new `short_code` is generated; trackable links from the source are NOT copied. Auto-match rules + activities + sent counts + scheduled_at are NOT copied. The merchant navigates to the new draft, edits the body, sends.

**Acceptance Scenarios**:

1. **Given** the merchant clicks "Duplicate" on any campaign, **When** the action completes, **Then** a new campaign is created with status=Draft and name suffixed " (Copy)"
2. **Given** the source campaign had subject / body / audience_filter / segment_id set, **When** the duplicate is created, **Then** those fields are copied verbatim
3. **Given** the source campaign had trackable links / auto-match rules / activities, **When** the duplicate is created, **Then** those are NOT copied (clean slate for the new campaign)
4. **Given** the duplicate is created, **When** the merchant opens it, **Then** they're shown the new campaign's detail page

---

### User Story 7 - Cross-campaign comparison (Priority: P3)

A merchant wants to know which of three recent Eid promotions performed best. They want a side-by-side view of all three campaigns' KPIs and over-time charts, not three browser tabs.

**Why this priority**: Real value but only for merchants running multiple campaigns concurrently. Most stores send 1-2 campaigns at a time. P3.

**Independent Test**: With comparison deployed, the merchant on the campaign list selects 2-4 checkboxes. A "Compare selected" button appears. Clicking it navigates to `/campaigns/compare?ids=a,b,c`. The page shows each campaign as a column with its name + KPI cards (Sessions / Sales / Orders / AOV) and a single overlaid line chart of sessions-over-time with one line per campaign.

**Acceptance Scenarios**:

1. **Given** the merchant is on the campaign list, **When** they check 2-4 campaign rows, **Then** a "Compare selected" CTA appears in the page header
2. **Given** fewer than 2 or more than 4 rows are checked, **When** the merchant looks at the CTA, **Then** it's disabled with helper text ("Pick 2 to 4 campaigns")
3. **Given** the merchant clicks "Compare selected", **When** the page loads, **Then** each selected campaign appears as a column with its KPI cards
4. **Given** the merchant adjusts the date range on the compare page, **When** the range updates, **Then** all campaign columns refresh together
5. **Given** the merchant accesses `/campaigns/compare?ids=a,b,c,d,e` directly, **When** the page validates the URL, **Then** the system rejects with "Pick at most 4 campaigns"

---

### User Story 8 - AI optimization tips (Priority: P3)

A merchant who isn't comfortable reading the chart panels wants a plain-English summary of what's working and what isn't: "Your facebook channel converts 3x better than instagram — consider shifting budget." The system surfaces 1-3 heuristic recommendations in a sidebar card.

**Why this priority**: Helpful nudge for less-analytical merchants, but the underlying data is already visible in the chart panels. Not load-bearing. P3.

**Independent Test**: With tips deployed, the merchant opens a campaign that has data across multiple channels. The right sidebar shows a "Tips" card with 1-3 collapsible items. Each tip is a short paragraph (1-2 sentences). Dismissing a tip hides it for that session. Tips are computed from real data — no LLM call.

**Acceptance Scenarios**:

1. **Given** a campaign has data across ≥2 channels and one has CVR > 2.5× the median, **When** the merchant opens the detail page, **Then** a "Boost {channel}" tip appears in the Tips card
2. **Given** > 70% of the campaign's revenue came via coupon redemptions, **When** the merchant opens the detail page, **Then** a "Coupon cannibalization" tip appears
3. **Given** the campaign's mobile session share < 30%, **When** the merchant opens the detail page, **Then** a "Mobile traffic skew" tip appears
4. **Given** the campaign's top product accounts for > 60% of revenue, **When** the merchant opens the detail page, **Then** a "Top-product concentration" tip appears
5. **Given** the merchant dismisses a tip, **When** the page is reloaded within the same session, **Then** the tip stays hidden
6. **Given** a new session, **When** the merchant returns, **Then** previously-dismissed tips reappear

---

### User Story 9 - Best-time-to-send picker (Priority: P3)

A merchant opens the Schedule dialog and wants a suggestion of the best time to send based on their store's own historical engagement, not a generic recommendation. They want chips showing the top 3 weekday × hour combos by open rate (or fallback to send-count habit when no open data exists).

**Why this priority**: Engagement uplift is real but small. Most merchants pick a time based on intuition or campaign deadline. P3.

**Independent Test**: With best-time deployed, the merchant clicks "Schedule" on a draft campaign. The dialog shows 3 chips like "Thu 7 PM (avg 42% open)" above the datetime picker. Clicking a chip sets the datetime picker to the next occurrence of that weekday × hour. If the store has no open-rate data (e.g., SMS-only history or new store), the chips fall back to "most-used send slots" with helper text "Based on your usual habit (no open data yet)."

**Acceptance Scenarios**:

1. **Given** a store has ≥10 prior email sends with open-rate data, **When** the merchant opens the Schedule dialog, **Then** they see 3 suggested-send-time chips ranked by avg open rate
2. **Given** a store has no open-rate data, **When** the merchant opens the Schedule dialog, **Then** the chips fall back to most-frequent historical send slots with explanatory text
3. **Given** the merchant clicks a suggested chip, **When** the dialog updates, **Then** the datetime picker is set to the next occurrence of that weekday × hour (e.g., next Thursday at 7 PM)
4. **Given** the merchant edits the datetime picker after picking a chip, **When** they submit, **Then** their edit is respected (the chip is just a starting point)
5. **Given** a store has < 10 prior sends, **When** the merchant opens the Schedule dialog, **Then** the chip row is hidden with helper text "Not enough send history for suggestions yet"

---

### Edge Cases

- **Campaign with no attributed traffic in window**: KPI cards show "—" and all 8 chart panels show "No data for this date range." (not blank, not "0" — explicit empty-state copy)
- **Auto-match rule conflict**: Two campaigns in the same store have overlapping rules. The store-global priority order determines which campaign wins. The system surfaces the conflict in the rule editor as a warning ("This rule overlaps with Campaign X — Campaign X has higher priority")
- **Backfill targets a row already attributed to a different campaign**: Row is skipped, NOT overwritten. The activity log reports the skip count separately from the affected count
- **Duplicate of a campaign in SENDING status**: Allowed. The duplicate is always a fresh Draft regardless of the source's status
- **Comparison with a deleted campaign in the URL**: System gracefully drops the deleted id from the comparison and shows a banner ("1 of 3 campaigns is no longer available")
- **Tips card hidden by the merchant for ALL 3 tips on a campaign**: Empty Tips card shows "No tips for this campaign right now."
- **Best-time chip clicked but the next occurrence is in the past**: Picker skips to the occurrence after next (e.g., if it's Thu 8 PM and chip says "Thu 7 PM", picker sets to next Thursday 7 PM, not today)
- **Cross-campaign compare with > 4 ids in URL**: 400 error with copy "Pick at most 4 campaigns"
- **Cross-campaign compare with campaigns from different stores**: 403 (auth scope already prevents cross-store reads, but the page validates explicitly)
- **RTL (Arabic) layout for all new panels**: Chart legend ordering, sidebar position, breadcrumb chevrons all mirrored correctly

## Requirements *(mandatory)*

### Functional Requirements

#### Navigation (US1)

- **FR-001**: System MUST present a "Marketing" collapsible parent in the merchant hub sidebar containing "Campaigns" and "Attribution" sub-items
- **FR-002**: System MUST preserve Email Templates and WhatsApp as top-level sidebar items (not nested under Marketing)
- **FR-003**: System MUST render the Marketing parent and sub-items in both English ("Marketing", "Campaigns", "Attribution") and Arabic ("التسويق", "الحملات", "الإسناد") with correct RTL ordering

#### Attribution page (US2)

- **FR-004**: System MUST provide a route `/marketing/attribution` that hosts both LTV-by-channel and Multi-touch dashboards as tabs
- **FR-005**: System MUST share a single date range picker across both Attribution tabs (changing the range updates both tabs' data)
- **FR-006**: System MUST redirect requests to legacy routes `/analytics/ltv` and `/analytics/multi-touch` to the corresponding tab on `/marketing/attribution`
- **FR-007**: System MUST preserve the attribution model selector on the Multi-touch tab with all 5 models (last_touch, first_touch, linear, time_decay, position_based)

#### Campaign Detail layout (US3)

- **FR-008**: System MUST render the campaign detail page with a sticky header containing breadcrumb, title, status badge, date range pill, attribution model pill, and action buttons (Send Now / Schedule / Cancel respecting the existing state-transition guards)
- **FR-009**: System MUST render a 320px right sidebar (collapsible to a drawer below 1024px viewport width) containing: editable campaign name, short_code with copy button, permanent Shareable Links section, Auto-match rules panel (US4), Campaign activities panel (US5), and Tips panel (US8)
- **FR-010**: System MUST render a main grid with 4 KPI cards in row 1 (Sessions, Sales, Orders, Average order value)
- **FR-011**: System MUST render a 2-column grid below the KPI cards with 8 chart panels: Sessions by channel, Sales by channel, Sessions by UTM parameters, Sales by UTM parameters, Orders from new vs returning customers, Sales by order, Items sold by product, Sessions by device
- **FR-012**: System MUST refresh all KPI cards and chart panels when the merchant changes the date range pill
- **FR-013**: System MUST refresh all KPI cards and panels when the merchant changes the attribution model pill
- **FR-014**: System MUST render an explicit "No data for this date range." message inside any panel whose query returned an empty result (not a blank or zero-filled chart)
- **FR-015**: System MUST persist the campaign name inline-edit when the merchant tabs out of the field and update the breadcrumb to match
- **FR-016**: System MUST derive device classification (desktop / mobile / tablet) from the funnel event's stored user-agent string

#### Auto-match rules (US4)

- **FR-017**: System MUST allow merchants to create per-campaign rules of the shape `{ field: utm_source | utm_medium | utm_campaign, operator: equals | starts_with | contains, value: string }`, composable with AND/OR
- **FR-018**: System MUST apply auto-match rules at funnel event ingestion BEFORE the existing short_code-based campaign resolution
- **FR-019**: System MUST defer to explicit short_code resolution when a funnel event simultaneously matches an auto-match rule AND carries a valid short_code (explicit beats implicit)
- **FR-020**: System MUST resolve auto-match priority store-globally (first-match-wins across all campaigns in the store, ordered by per-rule priority field)
- **FR-021**: System MUST surface overlapping-rule warnings in the rule editor when a new rule would collide with an existing higher-priority rule
- **FR-022**: System MUST NOT retroactively attribute historical funnel events on rule creation (use US5 for backfill)
- **FR-023**: Users MUST be able to delete an auto-match rule, after which new traffic stops being attributed to the campaign via that rule

#### Campaign activities / manual backfill (US5)

- **FR-024**: System MUST provide a backfill action that updates `orders.campaign_id` and `funnel_events.campaign_id` for past rows matching a merchant-defined filter within a date window
- **FR-025**: System MUST skip (not overwrite) rows that already have a non-null `campaign_id` pointing to a different campaign
- **FR-026**: System MUST log every backfill action with timestamp, filter snapshot, affected_count, and skipped_count; the log is viewable in the right-sidebar activities panel
- **FR-027**: System MUST make backfills idempotent (running the same backfill twice on unchanged data produces 0 additional affected_count on the second run)
- **FR-028**: System MUST cap the backfill window at 365 days to prevent unbounded full-table scans

#### Duplicate (US6)

- **FR-029**: System MUST provide a "Duplicate" action that creates a new Draft campaign copying: name (with " (Copy)" suffix), channel, inline_subject, inline_body, audience_filter, segment_id, template_id
- **FR-030**: System MUST mint a new short_code for the duplicate and NOT copy: trackable links, auto-match rules, activities, scheduled_at, started_at, completed_at, sent_count, delivered_count, failed_count, status (always Draft)
- **FR-031**: System MUST surface the Duplicate action on both the campaign list row (on hover) and the detail page header

#### Cross-campaign comparison (US7)

- **FR-032**: System MUST provide a route `/campaigns/compare?ids=a,b,c[,d]` that accepts 2 to 4 campaign ids
- **FR-033**: System MUST render each comparison campaign as a column with its KPI cards (Sessions, Sales, Orders, AOV) and a single overlaid line chart showing sessions over time, one line per campaign
- **FR-034**: System MUST enable multi-select checkboxes on the campaign list with a "Compare selected" CTA that's enabled only when 2-4 rows are checked
- **FR-035**: System MUST reject URLs with > 4 ids or < 2 ids with copy "Pick 2 to 4 campaigns"
- **FR-036**: System MUST gracefully handle deleted campaigns in the URL by dropping them and surfacing a banner ("N of M campaigns are no longer available")

#### AI optimization tips (US8)

- **FR-037**: System MUST compute and surface 0-3 heuristic tips per campaign in the Tips sidebar card, computed from the campaign's existing aggregations (no external LLM call required)
- **FR-038**: System MUST support these tip types: Outperforming channel (max_channel_cvr > 2.5× median), Coupon cannibalization (coupon_revenue / total_revenue > 0.7), Mobile traffic skew (mobile_session_pct < 0.3), Top-product concentration (top_product_revenue / total_revenue > 0.6)
- **FR-039**: System MUST allow merchants to dismiss individual tips for the current session; dismissed tips reappear on next session

#### Best-time-to-send picker (US9)

- **FR-040**: System MUST display up to 3 suggested-send-time chips in the Schedule dialog, ranked by historical avg open rate computed from the last 90 days of the store's sent campaigns
- **FR-041**: System MUST fall back to most-frequent-send-slot chips when open-rate data is unavailable, with explanatory helper text
- **FR-042**: System MUST hide the chip row (with explanatory copy) when the store has < 10 prior sends
- **FR-043**: System MUST resolve a chip click to the NEXT occurrence of the suggested weekday × hour, skipping to the occurrence-after-next when the immediate next is in the past

### Key Entities *(include if feature involves data)*

- **CampaignAutoMatchRule**: Per-campaign matching rule. Attributes: campaign_id (FK), field (utm_source|utm_medium|utm_campaign), operator (equals|starts_with|contains), value (string), combinator (AND|OR), priority (int, store-globally ordered), created_at, created_by
- **CampaignActivity**: Audit log of merchant-initiated campaign actions (backfills today, extensible for future). Attributes: campaign_id (FK), type (enum: backfill_attribution today), payload (JSONB filter snapshot), affected_count, skipped_count, run_at, run_by, status (running|completed|failed)
- **Existing entities reused**:
  - MarketingCampaign (existing) — no schema change beyond related-row backfill semantics
  - Order (existing) — `campaign_id` already nullable FK; backfill updates this column
  - FunnelEvent (existing) — `campaign_id` already nullable FK; backfill updates this column
  - Customer (existing) — `first_touch_attribution` JSONB and `first_touch_at` used to classify new vs returning
  - Coupon (existing) — `campaign_id` FK feeds Coupon Cannibalization tip
  - CustomerTouch (existing) — feeds attribution-model-aware credit computation on the campaign detail page

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A merchant can navigate from the hub home to the Campaigns list in ≤ 1 click (via the Marketing parent → Campaigns sub-item)
- **SC-002**: A merchant can view all 8 KPI/chart panels for a campaign without scrolling between tabs or routes (single page render)
- **SC-003**: Date range or attribution model changes on the campaign detail page propagate to all visible panels in ≤ 800ms p95 (over a 30-day window, single-store)
- **SC-004**: Empty windows show explicit "No data for this date range." copy in 100% of panels (no blank cards, no misleading "0" values)
- **SC-005**: A merchant can duplicate a campaign in ≤ 2 clicks and land on the new draft's detail page within 1s
- **SC-006**: Auto-match rule creation followed by an incoming matching funnel event attributes the event within 1 ingestion cycle (no backfill needed)
- **SC-007**: Manual backfill runs report a definitive affected_count and skipped_count in the activities log within 30 seconds for a single-campaign 90-day window (single-store scale)
- **SC-008**: AI tips appear on ≥ 60% of campaigns that have ≥ 100 attributed sessions (most active campaigns surface at least one heuristic)
- **SC-009**: Tip dismissals persist for the session (no flicker / re-appearance on tab switch)
- **SC-010**: Best-time-to-send chips appear in the Schedule dialog within 200ms of dialog open (precomputed or cached per store, not query-per-open)
- **SC-011**: The merchant hub passes RTL visual regression on the new Marketing nav, Campaign detail layout, Attribution page, and Compare page (no overlapping text, correct chevron direction, mirrored chart legends)
- **SC-012**: Cross-campaign comparison renders 4 campaigns × 4 KPI cards + 1 overlaid line chart in ≤ 1500ms p95 (single-store, 30-day window)
- **SC-013**: Zero regressions on existing campaign send / schedule / cancel flows (existing acceptance tests for PR #116, #117, #119, #120, #121 all still pass)
- **SC-014**: A merchant who saw the original tabbed campaign detail page can identify and use the new Shareable Links sidebar within 10 seconds on first exposure (usability validation — measured via a 5-person session test on test env)

## Assumptions

- **Visual reference**: Shopify's Marketing > Campaigns > Create campaign view is the layout reference. NUMU is not pixel-cloning Shopify; the structural pattern (header pills + right sidebar + KPI grid + chart grid) is the model.
- **Backend data shape**: All 8 chart panels need new aggregation queries on AnalyticsRepository. Three are extensions of existing endpoints; five are net-new. Device classification needs a user-agent column on funnel_events (or derives from existing request metadata at ingest time).
- **New vs returning definition**: "New" = customer's first attributed order is this campaign's first attributed order for them. "Returning" = customer has ≥ 1 prior attributed order on the store (regardless of which campaign).
- **Auto-match rules historical scope**: Rules apply ONLY to NEW traffic from rule-create time forward. Historical backfill is the merchant's explicit choice via US5, NOT an implicit side-effect of creating a rule. (Avoids surprise large updates and overlap with running attribution recomputation.)
- **Auto-match rule priority**: Store-globally ordered (per-store priority int), first-match-wins across all campaigns. Per-campaign priority would let merchants game ordering by deleting+recreating rules; store-global is simpler and harder to abuse.
- **Best-time-to-send open-rate source**: Open-rate data comes from the existing per-send delivery logs where available (Resend webhook for email, no opens for SMS). Stores without open data get the fallback (send-count habit ranking).
- **Duplicate copy scope**: Trackable links and auto-match rules are NOT copied — they're campaign-specific identifiers that would conflict if duplicated (same short_code can't be used twice). The merchant must re-create them in the new draft if needed.
- **Cross-campaign comparison cap**: 4 campaigns max. Beyond 4, the overlaid line chart becomes unreadable and the column layout doesn't fit common laptop widths. Hard cap enforced server-side.
- **Tip dismissal scope**: Session-only (localStorage with session key, not synced to backend). Merchant comes back tomorrow → tips reappear. Avoids backend state for ephemeral UI.
- **AI tips composition**: All tips are deterministic heuristic rules computed from existing aggregations. No LLM call, no external service. Future enhancement could layer an LLM polish over the headline + body, but v1 is rule-based for cost, latency, and audit reasons.
- **Existing campaign send flow**: Send Now / Schedule / Cancel buttons already exist (PR #123) and stay as-is. This feature does NOT change campaign lifecycle semantics.
- **Storefront-side changes**: None required for US1-US9 except for the user-agent capture on funnel_events (US3 panel "Sessions by device"). If the storefront SDK already captures UA, no SDK change needed; if not, the storefront emits an additional field.
- **Performance**: Date range queries default to last 30 days. Larger windows (90+ days) are allowed but render with a soft warning above the 30-day default. The 1-year cap on backfill (FR-028) prevents pathological scans.
- **Multi-tenant scope**: Single store at a time. All endpoints are scoped to `/stores/{store_id}/...` with the existing tenant guard. No agency / multi-store-comparison flow in this feature.
