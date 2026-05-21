# Quickstart — UTM & Campaign Attribution

End-to-end verification recipe. Once implementation is complete, walking through this on a fresh test environment should exercise every user story in the spec and prove the data flows correctly.

---

## Prerequisites

- A test store on the test environment (`acme-test.numueg.app` style)
- A merchant account with access to that store
- Two products in the catalog (one will be the campaign destination, one will be browsed after landing)
- A second device (or a fresh incognito window) to play "shopper"
- Optional: `psql` access to the test DB for the spot-checks

---

## Step 1 — Create a campaign (merchant hub)

1. Log into the merchant hub at `merchant-test.numueg.app`.
2. Navigate to **Marketing → Campaigns**.
3. Click **New campaign**. Channel: `email`. Name: `Eid Sale 2026 — Quickstart`.
4. Save as draft.

**Expected**: campaign row appears. Note the auto-generated `short_code` (6 base32 chars, e.g. `AB7K9X`).

**DB spot-check**:
```sql
SELECT id, name, short_code, status FROM marketing_campaigns
WHERE name LIKE '%Quickstart%' ORDER BY created_at DESC LIMIT 1;
```
Expect: one row, `short_code` non-null, 6 chars from the Crockford alphabet.

---

## Step 2 — Generate a trackable link

1. Open the campaign detail page.
2. Click the **Trackable Links** tab.
3. **Destination**: Product → search and pick one of your products.
4. **Source preset**: Facebook.
5. Click **Generate**.

**Expected**:
- A copy-pasteable URL appears, of the shape:
  `https://acme-test.numueg.app/product/<id-or-slug>?utm_source=facebook&utm_medium=social&utm_campaign=eid-sale-2026-quickstart-AB7K9X`
- A QR PNG renders next to it, with a download button.
- Copying the URL into a new tab and visiting it must load the product page (200 OK, no broken layout).

**Backend spot-check** (optional):
```bash
curl -X POST \
  -H "Authorization: Bearer $MERCHANT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"destination":{"kind":"product","product_id":"<id>"},"source":"facebook"}' \
  https://api-test.numueg.app/api/v1/stores/<store_id>/campaigns/<campaign_id>/trackable-link
```
Response shape matches `contracts/merchant-campaign-api.md`.

---

## Step 3 — Simulate a campaign click (second device / incognito)

1. Open the trackable URL on the second device (or fresh incognito).
2. Verify the product page loads correctly.
3. Open DevTools → Application → Cookies → look for `numu_attribution`.
4. Decode the cookie value (URL-decode + JSON-parse).

**Expected cookie payload**:
```json
{
  "v": 1,
  "first_touch": {
    "ts": "<recent ISO timestamp>",
    "utm_source": "facebook",
    "utm_medium": "social",
    "utm_campaign": "eid-sale-2026-quickstart-AB7K9X",
    "referrer": "",
    "landing_path": "/product/<id-or-slug>"
  },
  "last_touch": { /* same shape, same values */ },
  "session_id": "<ULID>"
}
```

---

## Step 4 — Browse before purchasing (the journey-persistence test)

Still on the second device:

1. Click the homepage logo. The URL changes to `/` — UTM params are gone.
2. Navigate to a different product (one that wasn't the campaign destination).
3. Add it to cart.

**DB spot-check**:
```sql
SELECT step, utm_campaign, campaign_id, referrer, created_at
FROM funnel_events
WHERE store_id = '<store_id>' AND session_fingerprint = '<from cookie session_id or fingerprint>'
ORDER BY created_at;
```

**Expected**: every row has `utm_campaign = 'eid-sale-2026-quickstart-AB7K9X'` and `campaign_id = <campaign_id>`, even though the URL no longer carries those params. The cookie kept the attribution alive.

---

## Step 5 — Complete a purchase

1. Open cart → checkout. Fill in shipping. Complete checkout (use COD for speed; payment flow is incidental to this test).

**Expected**: order confirmation page appears.

**DB spot-check**:
```sql
SELECT id, order_number, utm_source, utm_campaign, campaign_id, first_touch_at,
       attribution->'first_touch'->>'utm_campaign' AS attribution_first_utm
FROM orders
WHERE order_number = '<just-placed>';
```

**Expected**:
- `utm_source = 'facebook'`
- `utm_campaign = 'eid-sale-2026-quickstart-AB7K9X'`
- `campaign_id` = the campaign's UUID
- `attribution.first_touch.utm_campaign` matches `attribution.last_touch.utm_campaign` (no later-touch overwrote it)
- `first_touch_at` is the timestamp from Step 3.

---

## Step 6 — Verify per-campaign performance dashboard

Back in the merchant hub:

1. Open the campaign detail page → **Performance** tab.
2. Set date range to "Last 7 days".

**Expected**:
- **Sessions**: 1 (or more, if you bounced around)
- **Add to cart**: 1
- **Checkouts started**: 1
- **Orders**: 1
- **Revenue**: matches the order total
- **AOV**: equals the order total (single order)
- **Conversion rates**: 100% across the funnel (session → ATC → checkout → order)
- **Top products**: the product you bought, with quantity 1

---

## Step 7 — Customer share button

1. On the second device, navigate to any product page.
2. Tap the WhatsApp share button.
3. Note the URL in the share intent — it should be tagged `utm_source=customer_share&utm_medium=whatsapp&utm_campaign=organic_share`.
4. Open that URL on yet another fresh window (or revoke the cookie first via DevTools).
5. Browse + complete a purchase.

**DB spot-check**:
```sql
SELECT utm_source, utm_campaign, campaign_id FROM orders
WHERE order_number = '<just-placed>';
```

**Expected**:
- `utm_source = 'customer_share'`, `utm_campaign = 'organic_share'`, `campaign_id IS NULL` (no campaign auto-created from this string — see FR-011).

**Merchant hub**: open analytics → traffic sources. `customer_share` appears as a distinct row. The campaign list in the campaigns section does NOT have a new `organic_share` entry.

---

## Step 8 — Custom destination path validation

In the campaign trackable-link panel:

1. Choose **Destination → Custom path**.
2. Enter a path that doesn't exist on the storefront (e.g. `/lookbook/does-not-exist`).
3. Click **Generate**.

**Expected**: a clear inline error like *"That page doesn't exist on your store. Try a different path."* — no link is produced, no QR is generated.

Then:

1. Enter a path that does exist (e.g. `/about`).
2. Click **Generate**.

**Expected**: link generates successfully, pointing to `/about` with UTM params.

---

## Step 9 — Privacy: declined-marketing consent visitor

1. Open a fresh incognito window. Land on the storefront. Accept-no-actually-decline the cookie banner.
2. Open the campaign-tagged URL from Step 2 on this same session.
3. Browse, add to cart, check out.

**Expected**:
- Cookie `numu_attribution` is still set (per FR-009, attribution is functional analytics regardless of marketing consent).
- The order is attributed to the campaign (same DB checks as Step 5 pass).
- Meta CAPI fan-out for this visitor either does not fire OR fires with `opt_out: true` (per the existing `opt_out` plumbing in `tracking.py`).
- The cookie banner copy reflects this: "We use cookies for X, Y, Z; advertising-partner sharing is governed by your consent choice."

---

## Common failure modes to watch for

| Symptom                                                                | Likely cause                                                                   |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Cookie not set after Step 3                                            | `useAttribution()` hook not mounted in tenant layout, or runs only server-side |
| Cookie set but funnel events show `campaign_id = NULL`                 | Server-side cookie parse failing, or `short_code` lookup query off-by-one      |
| Order has `utm_campaign` but `campaign_id = NULL`                      | Short_code suffix split logic broken (campaign string doesn't end in `-XXXXXX`)|
| Performance dashboard shows 0 sessions but order count is correct      | Funnel-event attribution columns not being written (check `tracking.py`)       |
| Custom-path validation accepts an invalid path                         | `validate-path` endpoint's HEAD request not respecting redirects properly      |
| `customer_share` becomes a campaign entry                              | FR-011 not enforced — auto-create logic was added by mistake                   |

---

## Performance sanity check

After running the above flow, the per-campaign performance dashboard should render in under 1 second on a clean test DB. If it takes longer:

- Verify the `(store_id, campaign_id, created_at)` partial index exists on both `orders` and `funnel_events`.
- `EXPLAIN ANALYZE` the dashboard's query — should be an index scan, not a sequential scan.

---

## What this quickstart does NOT cover

- Cross-device journeys (out of scope per non-goals).
- Multi-touch attribution windows (last-touch only in v1).
- Coupon ↔ campaign FK (deferred to v2).
- Short-link redirector (`numueg.app/r/xyz`) (deferred).
- Backfill of pre-existing customers' `first_touch_attribution` from historical orders (optional one-shot script, not part of acceptance).
