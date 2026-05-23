# Quickstart — Manual verification

After implementation, this script proves the entire feature works end-to-end on the **test env** (`test.numueg.app`). Estimated time: 20 min.

## Prereqs

| | |
| --- | --- |
| Logged-in merchant on `hub.test.numueg.app` (or whichever subdomain) | ✓ |
| Incognito window pointed at the test store's storefront | ✓ |
| DevTools open in the storefront window (Application + Network tabs) | ✓ |
| At least 10 prior test sends for the store (otherwise US9 chips will be hidden — expected) | — |

---

## Phase 1 — Nav restructure (US1) + Attribution page (US2)

| # | Action | Pass criterion |
| - | ------ | -------------- |
| 1 | Hub → look at sidebar | "Marketing" parent visible (collapsible); contains "Campaigns" + "Attribution" sub-items |
| 2 | Click "Marketing" | Collapses/expands; clicking again toggles |
| 3 | Click "Attribution" | Lands on `/marketing/attribution` with date range picker + 2 tabs (LTV, Multi-touch) |
| 4 | Change date range, switch tabs | Date range preserved across tabs |
| 5 | Navigate to `/analytics/ltv` directly | Redirects to LTV tab on `/marketing/attribution` |
| 6 | Navigate to `/analytics/multi-touch` directly | Redirects to Multi-touch tab |
| 7 | Switch hub to Arabic | Marketing group label reads "التسويق"; RTL order correct |

---

## Phase 2 — Shopify-style Detail layout (US3)

| # | Action | Pass criterion |
| - | ------ | -------------- |
| 8 | Hub → Marketing → Campaigns → open a campaign with attributed traffic | Sticky header with breadcrumb, status badge, date range pill, attribution model pill, Send Now/Schedule/Cancel buttons |
| 9 | Look at right sidebar (320px) | Campaign name (inline editable), short_code with copy button, Shareable Links section, Auto-match rules panel, Activities panel, Tips panel |
| 10 | Read main grid | Row 1: 4 KPI cards (Sessions / Sales / Orders / AOV). Below: 2-col grid with 8 chart panels |
| 11 | Change date range pill | All 4 KPI cards + 8 chart panels refresh |
| 12 | Change attribution model pill | All cards + panels recalculate |
| 13 | Pick a date range with no data | Each panel shows "No data for this date range." (not blank, not 0) |
| 14 | Inline-edit campaign name in sidebar, tab away | Name persists; breadcrumb updates |
| 15 | Resize browser to < 1024px | Right sidebar collapses to a drawer toggle button; chart grid becomes 1-col |
| 16 | Look at Sessions by device panel | Donut with mobile / desktop / tablet (+ unknown if historical data) |

---

## Phase 3 — Auto-match rules (US4)

| # | Action | Pass criterion |
| - | ------ | -------------- |
| 17 | Sidebar → Auto-match rules → "+ Add rule" | Modal opens; fill `field=utm_source, operator=equals, value=partner-x`; priority=50; save |
| 18 | From the storefront incognito window, visit `?utm_source=partner-x&utm_medium=cpc` URL | Cookie sets normally (no short_code resolution); reload + check Network for `/track` calls |
| 19 | Backend: query `SELECT campaign_id FROM funnel_events WHERE store_id=... ORDER BY created_at DESC LIMIT 5;` | The latest events have `campaign_id = <Campaign>.id` |
| 20 | Create a 2nd rule on another campaign with overlapping conditions | The editor surfaces a "rule overlaps" warning naming the higher-priority campaign |
| 21 | Send a synthetic event with `?utm_source=partner-x&utm_campaign=<short_code>` | The short_code wins (FR-019); event's `campaign_id` = short_code's campaign |
| 22 | Delete the rule | New events with `utm_source=partner-x` no longer attribute to the campaign |

---

## Phase 4 — Manual attribution backfill (US5)

| # | Action | Pass criterion |
| - | ------ | -------------- |
| 23 | Sidebar → Campaign activities → "Run backfill" | Modal opens with filter editor + date range |
| 24 | Submit `utm_source = instagram` over last 30 days | Backfill kicks off; activity row appears with `status=running` |
| 25 | Wait ≤ 30s; refresh activities list | Activity status flips to `completed`; reports `affected_count + skipped_count` |
| 26 | Run the same backfill again | Reports `affected_count = 0`, `skipped_count > 0` (idempotency, FR-027) |
| 27 | Inspect a backfilled order in DB | `orders.campaign_id` = this campaign's id |
| 28 | Try to run a backfill with a 400-day window | UI shows error: "Window cannot exceed 365 days" (FR-028) |
| 29 | Try to run a concurrent backfill on the same campaign | UI shows 409 error: "Backfill already running" |

---

## Phase 5 — Duplicate (US6)

| # | Action | Pass criterion |
| - | ------ | -------------- |
| 30 | Hub → Campaigns → hover a campaign row → click Duplicate | New draft created with name suffixed " (Copy)" |
| 31 | Open the new draft | Same channel / subject / body / audience / segment; status=Draft; fresh short_code; NO trackable links carried over; NO auto-match rules; empty activity log |
| 32 | Click Duplicate from the detail page header (not the list) | Same outcome — works from both surfaces |

---

## Phase 6 — Cross-campaign comparison (US7)

| # | Action | Pass criterion |
| - | ------ | -------------- |
| 33 | Campaigns list → check 3 campaigns | "Compare selected" CTA appears |
| 34 | Click "Compare selected" | Lands on `/campaigns/compare?ids=a,b,c` |
| 35 | Read the page | 3 columns each with 4 KPI cards; below: 1 overlaid line chart with 3 lines (sessions over time) |
| 36 | Change the date range at the top | All 3 columns + chart refresh together |
| 37 | Visit `/campaigns/compare?ids=a` (1 id) | 400 error: "Pick 2 to 4 campaigns" |
| 38 | Visit `/campaigns/compare?ids=a,b,c,d,e` (5 ids) | 400 error: "Pick at most 4 campaigns" |
| 39 | Visit `/campaigns/compare?ids=a,b,<deleted>` | Page renders 2 columns + a banner: "1 of 3 campaigns is no longer available" |

---

## Phase 7 — AI optimization tips (US8)

| # | Action | Pass criterion |
| - | ------ | -------------- |
| 40 | Open a campaign with ≥ 2 channels of data | Tips panel shows 0-3 tips |
| 41 | If `boost-channel` triggered | Tip body cites the winning channel + CVR ratio |
| 42 | Dismiss a tip | Tip disappears for the session |
| 43 | Open a new tab, navigate to the same campaign | Dismissed tip stays hidden (session-scoped storage) |
| 44 | Restart the browser, navigate back | Dismissed tips reappear (session-only, not persisted to backend) |

---

## Phase 8 — Best-time-to-send picker (US9)

| # | Action | Pass criterion |
| - | ------ | -------------- |
| 45 | On a draft email campaign, click Schedule | Dialog opens with a "Suggested send times" chip row (or helper text if < 10 prior sends) |
| 46 | If chips visible — click "Thu 7 PM (avg 42% open)" | The datetime picker jumps to the next Thursday at 7 PM in store's tz |
| 47 | If today is Thursday at 6 PM — click "Thu 5 PM" chip | Picker advances to NEXT Thursday's 5 PM (FR-043 occurrence-after-next) |
| 48 | Edit the datetime picker after picking a chip | Edits respected (chip was just a starting point) |
| 49 | On an SMS draft, open Schedule | Chips hidden OR show "based on send-count habit" helper text (no email open data for SMS) |

---

## Phase 9 — Negative + regression checks

| # | Action | Pass criterion |
| - | ------ | -------------- |
| 50 | Existing endpoints still work: `GET /performance`, `POST /trackable-link`, send/schedule/cancel | All unchanged (PR #123 surface remains) |
| 51 | Existing LTV + Multi-touch endpoints still return data | Old endpoints work; new pages just consolidate them |
| 52 | Visit `/r/<short_code>` on test | 302 → canonical URL with UTMs baked in (short-link redirector unchanged) |
| 53 | Hit a tip endpoint with garbage attribution model: `?attribution_model=banana` | 422 (FastAPI validation) |
| 54 | RTL pass on every new page (Marketing nav, Detail layout, Attribution, Compare) | Chevrons mirror correctly; right sidebar moves to the left; chart legends reorder |

---

## Backend smoke (CLI)

Single command that hits every new read endpoint (write endpoints excluded from smoke):

```bash
JWT=...  # merchant token
STORE_ID=...
CAMPAIGN_ID=...

curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/campaigns/$CAMPAIGN_ID/breakdown/channel?date_from=2026-04-24T00:00:00Z&date_to=2026-05-24T23:59:59Z" -H "Authorization: Bearer $JWT" | jq .data.channels[0]
curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/campaigns/$CAMPAIGN_ID/breakdown/utm?date_from=...&date_to=..." -H "Authorization: Bearer $JWT" | jq .data.combos
curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/campaigns/$CAMPAIGN_ID/breakdown/customer-type?date_from=...&date_to=..." -H "Authorization: Bearer $JWT" | jq .data
curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/campaigns/$CAMPAIGN_ID/breakdown/order-size?date_from=...&date_to=..." -H "Authorization: Bearer $JWT" | jq '.data.bins | length'
curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/campaigns/$CAMPAIGN_ID/breakdown/device?date_from=...&date_to=..." -H "Authorization: Bearer $JWT" | jq .data.devices
curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/campaigns/$CAMPAIGN_ID/tips?date_from=...&date_to=..." -H "Authorization: Bearer $JWT" | jq .data.tips
curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/campaigns/$CAMPAIGN_ID/auto-match-rules" -H "Authorization: Bearer $JWT" | jq .
curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/campaigns/$CAMPAIGN_ID/activities?limit=10" -H "Authorization: Bearer $JWT" | jq .
curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/send-time-suggestions?channel=email" -H "Authorization: Bearer $JWT" | jq .data
curl -fsS "https://test.numueg.app/api/v1/stores/$STORE_ID/marketing/campaigns/compare?ids=A,B&date_from=...&date_to=..." -H "Authorization: Bearer $JWT" | jq .data.campaigns
```

Each command returns a 200 with a meaningful payload — no 500s.

---

## Done when

- All 54 manual steps pass
- The backend smoke script returns 200 on every line
- The pre-merge security review (`/speckit-security-review-branch`) finds no high/critical issues
- The version-guard validate (`/speckit-version-guard-validate`) passes (no-op on this feature since NUMU-api has no npm deps)
