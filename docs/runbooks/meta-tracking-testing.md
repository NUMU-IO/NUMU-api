# Meta Tracking — End-to-End Testing Guide

How to test the full Meta integration on `merchant-test.numueg.app`, from
getting credentials out of Meta Business Manager all the way to verifying
Custom Audiences sync, Lookalikes, and per-campaign attribution.

This is a step-by-step QA guide. Work it top-down — each tier depends on
the one above it.

---

## 0. Prerequisites — what you need before you start

| Thing | Where it comes from | Time to get |
|------|---------------------|-------------|
| Meta Business account | https://business.facebook.com | 5 min |
| Meta Ad Account | Created inside Business Manager | 5 min |
| Meta Pixel (Dataset) | Events Manager → "Connect data" → Web | 2 min |
| CAPI access token | Business Settings → System Users → Generate token | 5 min |
| NUMU test merchant | Sign up at `merchant-test.numueg.app` | 2 min |
| Test store on that account | Auto-created on first sign-in | — |

You do **not** need a real ad-spend budget. Everything in this guide either
runs against Meta's free APIs or creates ads in **PAUSED** state. Nothing
will burn money unless you flip those ads to ACTIVE yourself in Ads Manager.

---

## 1. Getting credentials from Meta Business Manager

### 1.1 Find your Pixel ID

1. Open https://business.facebook.com/events_manager
2. If you don't have a Pixel yet:
   - Click **Connect data sources** → **Web** → **Get started**
   - Pick a name (e.g. *NUMU Test Store*) and create
3. Once you have one, the **Pixel ID** is the 15–16 digit number in the
   left sidebar under the dataset name. Looks like `1234567890123456`.
4. Copy it. You'll paste this into NUMU.

> **⚠ Skip the "Set up a Meta pixel" wizard if it appears.** Meta will
> sometimes pop up an *Install code manually* wizard with three steps —
> Copy base code, Paste base code, Optimise setup. **Close it / hit the
> X.** NUMU's storefront already loads the Meta Pixel `fbevents.js`
> snippet automatically; all you provide is the numeric **Pixel ID**.
> You never paste JavaScript into NUMU. If you can't find the ID in the
> sidebar, click the dataset name → **Settings** tab → **Dataset ID** is
> at the top.

### 1.2 Find your Ad Account ID

1. Open https://adsmanager.facebook.com
2. Top-left dropdown shows the active ad account. The ID is the number
   after `act_` — e.g. `act_987654321` → ID is `987654321`.
3. If you have no ad account: Business Settings → **Accounts → Ad accounts
   → Add → Create a new ad account**. Pick currency = EGP, timezone =
   Africa/Cairo, payment = "I'll add later" (you don't need to spend).

### 1.3 Generate a CAPI access token (System User Token)

This is the long-lived token NUMU uses to send server-side events and to
sync Custom Audiences. **Do not use a personal user token** — they expire
in 60 days and break the integration.

1. Open https://business.facebook.com/settings
2. **Users → System Users → Add** → name it `NUMU Server` → role *Admin*
3. Click the new system user → **Add Assets** → pick your Pixel and your
   Ad Account → grant **Manage** access on both
4. Click **Generate new token**:
   - **App**: pick any app, or create a placeholder app first (System
     Users need an associated app — any app works, even a non-Meta-reviewed
     one for testing)
   - **Token expiry**: choose **Never**
   - **Scopes**: tick `ads_management`, `ads_read`, `business_management`,
     `pages_read_engagement`
   - Click **Generate token**
5. **Copy the token now.** Meta will not show it again. If you lose it
   you have to regenerate.

> **⚠ "No permissions available" at the Assign permissions step?**
> Meta is telling you the System User has no role on the app you picked,
> so it can't grant any scopes. Fix:
>
> 1. Close the token wizard.
> 2. **Business Settings → Apps** (left sidebar) → click the app you
>    picked → **Add People** / **System Users** tab → pick your System
>    User → grant **Developer** or **Admin** → Save.
> 3. Retry the token wizard. The scope checkboxes will now appear.
>
> If you don't have an app yet: https://developers.facebook.com/apps →
> *Create App* → use case **Other** → type **Business** → name it
> `NUMU Server` → create. Then **Business Settings → Apps → Add →
> Connect an existing app ID** → paste the new App ID. Then assign the
> System User as Developer (step 2 above). System User tokens do **not**
> require Meta App Review — App Review only matters for end-user OAuth.

You should now have three strings:
- Pixel ID: `1234567890123456`
- Ad Account ID: `987654321`
- CAPI access token: `EAAB...` (long, starts with `EAA`)

---

## 2. Connect Meta to NUMU

1. Log in at https://merchant-test.numueg.app
2. Pick your test store (the one your trackable storefront subdomain
   maps to, e.g. `<yourstore>.test.numueg.app`)
3. Go to **Settings → Tracking & Pixels** (or directly to
   `/settings/tracking`)

You'll see the Meta panel with three activation modes:

| Mode | What it does | When to use |
|------|--------------|-------------|
| **Browser pixel only** | Storefront fires the `fbq()` JS pixel | Quick start, no CAPI scopes needed |
| **Server (CAPI) only** | NUMU's backend sends events to Meta's CAPI endpoint | iOS 14.5+ / ad-blocker resilient |
| **Browser + Server** | Both fire, deduplicated by `event_id` | **Recommended** — best signal |

For full QA pick **Browser + Server**, then fill:

- **Pixel ID**: paste the 15–16 digit ID
- **CAPI access token**: paste the System User token (it'll be masked
  after save)
- **Test event code** (optional): `TEST12345` — when set, events show up
  on Events Manager's *Test events* tab instead of polluting production
  data. Highly recommended during QA.
- **Debug logging**: ON during QA, OFF in prod

Click **Save**. The status badge should flip to **Connected** within a
second.

> **Note:** the Ad Account ID is captured on the *Audiences* page (Tier 5),
> not on this panel — the tracking panel only needs Pixel + CAPI token.
> Audience features and Promote-on-Meta need the Ad Account ID too.

---

## 3. Tier 1 — Browser pixel fires on the storefront

**Goal:** confirm `fbq('track', 'PageView')` reaches Meta when a visitor
loads your storefront.

1. Open Events Manager → your Pixel → **Test events** tab
2. In the input at the top, paste your storefront URL:
   `https://<yourstore>.test.numueg.app`
3. Click **Open website**
4. In the browser tab that opens, browse a product or two
5. Back in Test events, you should see real-time rows: `PageView`,
   `ViewContent`, etc.

**If nothing appears:** open browser DevTools → Network → filter `facebook`.
You should see requests to `connect.facebook.net/...fbevents.js` and then
`facebook.com/tr/?id=<pixel-id>&ev=PageView`. If the script isn't loading,
the storefront `MetaPixel` component didn't get the Pixel ID — check
that the settings panel actually saved (refresh `/settings/tracking`).

---

## 4. Tier 2 — Server-side CAPI events

**Goal:** confirm NUMU's backend can POST to Meta's CAPI endpoint with
your token.

1. On `/settings/tracking`, scroll to the **Test event** button (right
   side of the panel)
2. Click **Send test event**
3. Within 5 seconds, Events Manager → Test events should show a row
   with `Source: Server` and event_name `Lead`

**If you get a 4xx error in the toast:**
- `OAuthException, code 190` → the CAPI token is wrong or expired. Regenerate.
- `OAuthException, code 200` → the token is missing `ads_management`. Re-run
  step 1.3 with the correct scopes.
- `(#100) Param ...` → the Pixel ID doesn't match the token's permissions.
  Make sure the System User has Manage access on this specific Pixel.

---

## 5. Tier 3 — Campaign send fires Lead events (US2)

**Goal:** sending a marketing campaign should ping Meta with one Lead
event per recipient.

1. Go to **Marketing → Campaigns → New campaign**
2. Pick a small audience (3–5 test recipients — your own emails)
3. Pick a product in the "What are you promoting?" picker
4. Click **Send now**
5. After the queue drains (~1 min), check Events Manager → Test events
6. You should see 3–5 `Lead` events, each with:
   - `event_id` = `mc_<campaign_id>_<customer_id>`
   - `custom_data.numu_campaign_id` = the campaign's short code (e.g. `S7p2bX`)

> Lead events are intentionally fire-and-forget. If CAPI fails the
> campaign send still succeeds (message delivery > analytics). Check the
> backend logs for warnings if events don't appear:
> `docker logs numu-api-test 2>&1 | grep -i "meta.capi"`

---

## 6. Tier 4 — UTM carry-through on Purchase (US3)

**Goal:** clicking the campaign's trackable link and purchasing should
attach the campaign's short code to the Meta Purchase event.

1. From the sent campaign's detail page, copy the trackable short link
   (e.g. `https://merchant-test.numueg.app/r/S7p2bX`)
2. Open the link in **incognito mode** (so you have a clean cookie jar)
3. It redirects to `<yourstore>.test.numueg.app/<product>` with
   `?utm_source=numu&utm_campaign=S7p2bX&...` in the URL
4. Add to cart → checkout → place an order (COD is fine — no real charge)
5. Open Events Manager → Test events
6. Find the `Purchase` event. Click it to expand. Under **Custom data**
   you should see:
   ```
   numu_utm_source: numu
   numu_utm_campaign: S7p2bX
   numu_campaign_id: S7p2bX
   numu_campaign_uuid: <uuid of the campaign>
   ```

**If these fields are missing:**
- The attribution cookie didn't capture. Check storefront DevTools →
  Application → Cookies → look for `numu_attribution`. It should have a
  JSON value with `utm_campaign: "S7p2bX"`.
- The Purchase CAPI fired before the cookie was read. This shouldn't
  happen in normal flows but if it does, check backend logs for
  `meta_capi_purchase_dispatcher`.

---

## 7. Tier 5 — Custom Audience sync (US4)

**Goal:** push a customer segment to Meta as a Custom Audience.

**Pre-step:** the Audiences page expects an Ad Account ID on the store.
The tracking panel doesn't set this. For now, set it via the API or DB:

```bash
# On the droplet:
docker exec -it numu-api-test python -c "
import asyncio
from sqlalchemy import text
from src.infrastructure.database.connection import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text('''
            UPDATE stores
            SET settings = jsonb_set(
                coalesce(settings, '{}'::jsonb),
                '{tracking,meta,ad_account_id}',
                '\"<YOUR_AD_ACCOUNT_ID>\"'
            )
            WHERE subdomain = '<yourstore>'
        '''))
        await s.commit()
asyncio.run(main())
"
```

Replace `<YOUR_AD_ACCOUNT_ID>` (digits only, no `act_` prefix) and
`<yourstore>`. The hub UI to set this from the panel is on the roadmap.

**Test:**

1. Go to **Marketing → Audiences**
2. You'll see three prebuilt segments:
   - **High LTV customers** — spent EGP 5,000+ lifetime
   - **Cart abandoners (30 days)** — added to cart, no purchase
   - **Lapsed customers (90 days)** — bought once, inactive 90+ days
3. Click **Sync** on any one. Wait for the toast.
4. Open https://business.facebook.com/adsmanager/audiences
5. You should see a new audience named:
   `NUMU · <your store> · High LTV customers`
   with member count > 0 (or 0 if your test data has no matching customers)

**If sync fails:**
- "Meta ad account is not connected" → the Ad Account ID isn't set on
  the store. Run the SQL above.
- "Meta refused to create the Custom Audience" → your CAPI token doesn't
  have `ads_management`. Regenerate the System User token with the
  correct scopes.
- Member count = 0 → expected if your test store has no orders. Seed
  some test orders first.

---

## 8. Tier 6 — Lookalike build (US5)

**Goal:** create a 1%/3%/5% lookalike from a synced Custom Audience.

**Pre-step:** the source audience must have **100+ matched users**.
Meta won't let you build a lookalike below that threshold. For test
stores you'll often need to seed customers first.

1. On **Marketing → Audiences**, find a row with `member_count >= 100`
2. Click the **Build lookalike** button → dialog opens
3. Pick sizes: 1%, 3%, or 5% (or multiple)
4. Pick country: default Egypt, or multi-select for MENA
5. Click **Create**
6. In Ads Manager → Audiences, you'll see new rows with status
   **In progress** (Meta needs 6–24 hours to build the lookalike)
7. Once status flips to **Ready**, the lookalike is usable for ad sets

**If the button is greyed out:** the source audience is below 100. Sync
more customers first, or pick a different segment.

---

## 9. Tier 7 — Promote-on-Meta (US7)

**Goal:** turn a sent campaign into a draft Meta ad without leaving NUMU.

1. Open a campaign with status **Completed** (i.e. has been sent)
2. On the detail page, the **Promote on Meta** button is at the top
3. Click it → confirm in the dialog
4. The toast gives you a deep-link to Ads Manager
5. Open the link → you should see:
   - A new **Ad creative** named after the campaign with the campaign's
     hero image
   - A new **Ad** in **PAUSED** state targeting the campaign's audience
     (the synced Custom Audience if one exists for the campaign's
     segment, otherwise broad targeting)

> Always PAUSED — NUMU never auto-launches paid spend. You explicitly
> flip it to ACTIVE in Ads Manager when you're ready to spend.

**If creation fails:**
- "Meta is not connected" → finish steps 1-3 of this guide first.
- "(#100) ad creative image_url..." → the campaign's hero image isn't
  publicly accessible. Storefront images on `test.numueg.app` are
  public; if you used a private image URL it'll fail.

---

## 10. Tier 8 — Per-campaign Meta attribution card (US6)

**Goal:** confirm the per-campaign attribution panel renders and points
to the right Custom Conversion in Events Manager.

1. Open a sent campaign's detail page
2. Above the chart grid, you should see the **Meta Attribution** card
3. It tells you a Custom Conversion was auto-created in Meta keyed off
   `custom_data.numu_utm_campaign EQUALS <short_code>`
4. The deep-link button opens Events Manager filtered to that conversion

**If the card is missing:**
- The campaign hasn't been sent yet (status ≠ Completed). The card only
  renders for Completed campaigns.
- Meta isn't connected on this store. Finish steps 1-2 of this guide.
- The custom conversion auto-create failed. Check backend logs:
  `docker logs numu-api-test 2>&1 | grep "meta_custom_conversion"`. The
  `marketing_campaigns.meta_custom_conversion_id` column will be NULL
  if it failed — the next send retries.

---

## 11. Verification queries (for the curious)

Confirm the right columns are populated end-to-end:

```sql
-- On numu-postgres-test:
SELECT
  mc.short_code,
  mc.status,
  mc.meta_custom_conversion_id,
  jsonb_pretty(mc.promoted_item) AS promoted_item
FROM marketing_campaigns mc
WHERE mc.tenant_id = '<your-tenant-uuid>'
ORDER BY mc.created_at DESC
LIMIT 5;
```

Expected after a full send:
- `meta_custom_conversion_id`: a Meta conversion id (15-digit string)
  or NULL if Meta isn't connected
- `promoted_item`: JSONB with `type` (product/collection/page) and a
  snapshot of the picker's selection at send time

```sql
-- Verify Purchase events got the attribution stitched:
SELECT
  o.id,
  o.utm_campaign,
  o.utm_source,
  o.campaign_id
FROM orders o
WHERE o.tenant_id = '<your-tenant-uuid>'
  AND o.utm_campaign IS NOT NULL
ORDER BY o.created_at DESC
LIMIT 5;
```

If `utm_campaign` is populated but the Purchase CAPI event in Events
Manager is missing the custom_data, the wiring broke between order
creation and CAPI dispatch — that's the dispatcher's job, see
`src/application/services/meta_capi_purchase_dispatcher.py`.

---

## 12. Common gotchas

| Symptom | Likely cause | Fix |
|--------|--------------|-----|
| 404 on `/settings/tracking` | Old build before PR #132 | Wait for CD; verify with `docker exec numu-merchant-hub-test ls /app/dist/assets \| grep Settings` |
| Test event never appears | Pixel ID mismatch | Re-copy the ID; check for trailing whitespace |
| "ads_management" scope errors | Token was generated as personal user | Regenerate via System User flow (step 1.3) |
| Audience created with 0 members | Test store has no matching customers | Seed orders or use a different segment |
| Custom Conversion not auto-created | First send happened before `meta_custom_conversion_id` column existed | Re-send the campaign |
| OAuth token expired (60 days) | Used personal token instead of System User | Regenerate as System User (Never expires) |

---

## 13. Out of scope (intentionally)

These are **not** covered by this integration in its current shape:

- Other ad platforms (Google Ads, TikTok Ads)
- Ad creative editing inside NUMU — we always hand off to Ads Manager
- Real-time attribution dashboards — the US6 card is cached, not live
- WhatsApp campaigns (separate Meta WhatsApp Business API integration,
  see `docs/runbooks/` for that runbook when it ships)
- Backfilling historical campaigns to Meta — too risky for rate limits
  and audience freshness
