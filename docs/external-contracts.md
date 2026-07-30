# External contracts — hardcoded assumptions about third parties

**Created:** 2026-07-30 (from the Meta/TikTok hardening pass — `docs/Plans/Meta&TikTok.md` §A.3.3.3)
**Owner:** NUMU platform

## Why this file exists

A merchant created a Meta Pixel whose ID is **17 digits**. NUMU rejected it, in
two layers, because we had encoded `^\d{15,16}$` as the "Meta Pixel ID format".
That bound came from third-party blog posts describing the IDs that existed
when those posts were written — Meta publishes no normative length, and
allocates IDs from a 64-bit space that grows over time. The rule was never
right; it just hadn't been wrong yet.

The same sweep found a Graph API version pinned to `v19.0`, deprecated ~18
months earlier, still shipping — and not failing, because an expired Graph
version doesn't error, it silently routes to whatever version Meta picks. And
nine OAuth URLs with a version literal that ignored the setting entirely.

The pattern is the same every time: **a constant that describes something a
third party owns, with no owner on our side and no expiry date.** It is correct
when written and rots in silence.

## The rule

> **If a constant describes something a third party owns, we do not enforce it —
> we bound it loosely and surface their error.**

Concretely:

- Validation regexes for third-party identifiers catch *paste errors* (`act_…`,
  URLs, letters in numeric fields, empty), nothing more. Bound by the real
  arithmetic constraint where one exists (64-bit → 20 digits), never by
  "lengths we have seen".
- One constant, one place. A rule retyped in three repos is a rule that will
  drift. Tracking rules now live in
  `src/api/v1/schemas/tenant/tracking_validation.py` and are served to
  the hub at `GET /stores/{id}/settings/tracking/validation-contract`.
- Prefer **asking the provider**. "Is this pixel ID valid?" is answered by
  `POST …/tracking/{meta,tiktok}/verify`, not by counting characters.
- Never leave a version fallback that can't fire. `settings.x or "v21.0"` reads
  as "we're on v21" to everyone who greps it, while the actual value is
  something else entirely.

## Register

| # | Assumption | Where | Current value | Review trigger |
|---|---|---|---|---|
| 1 | Meta Graph + Marketing API version | `src/config/settings.py` (`meta_graph_api_version`) | `v25.0` | **Quarterly**, and on any Meta changelog release. Marketing API versions expire in ~1 year vs Graph's ~2 — the Marketing clock binds. |
| 2 | Meta pixel-ID / token / test-code formats | `src/api/v1/schemas/tenant/tracking_validation.py` | `^\d{6,20}$`, min token 20, `^[A-Za-z0-9_-]{1,64}$` | On any merchant validation complaint. Loosen, don't tighten. |
| 3 | TikTok Events API path | `src/infrastructure/messaging/tasks/tiktok_capi.py:45` | `open_api/v1.3/event/track/` | Quarterly. v1.3 is current for Events API 2.0 as of 2026-07. |
| 4 | TikTok pixel-code format | `tracking_validation.py` | `^[A-Za-z0-9]{6,40}$` | Quarterly. Consistent across all three repos — keep it that way. |
| 5 | TikTok success signal is body `code == 0`, not HTTP 200 | `tasks/tiktok_capi.py`, `routes/stores/settings.py` (verify endpoint) | `code == 0` | On TikTok API-version bump. |
| 6 | WhatsApp Business (Cloud) API version | `src/config/settings.py` (`whatsapp_business_api_version`) | `v21.0` | **Quarterly — and this one is overdue.** Separate contract from #1, with its own cadence and template semantics; deliberately NOT changed in the Meta/TikTok pass because message-template behaviour is version-sensitive. Needs its own verification. |
| 7 | Meta `fbc` cookie format | `src/api/v1/routes/storefront/tracking.py` (`_synthesize_fbc`) | `fb.1.{ms}.{fbclid}` | On Meta CAPI doc change. Subdomain index 1 assumes a normal `store.example.com` host. |
| 8 | Storefront Meta pixel-ID bound | `numu-storefront/src/lib/meta-pixel.ts:35` | `^\d{6,20}$` | With #2. Not yet driven by the contract endpoint — the storefront has no authenticated session to fetch it. |
| 9 | Storefront TikTok pixel-code bound | `numu-storefront/src/lib/tiktok-pixel.ts:31` | `^[A-Za-z0-9]{6,40}$` | With #4. Same caveat as #8. |
| 10 | Egyptian tax ID | `src/infrastructure/external_services/tax/egyptian_tax_service.py:244` | `^\d{9}$` | On ETA spec change. **Same risk class as the pixel-ID bug** — an ETA-issued ID that grows past 9 digits fails identically. Not audited in this pass. |
| 11 | Saudi VAT number | `.../tax/saudi_tax_service.py:233` | `^\d{15}$` | On ZATCA spec change. ZATCA does document 15 digits, so this one is a real published constraint — unlike #10 and the old pixel rule. |
| 12 | Meta / TikTok browser SDKs | vendor CDN (`fbevents.js`, `pixel.js`) | auto-updating | None — nothing pinned, nothing to maintain. |

## Review checklist

When a review is triggered, for each row:

1. Confirm the third party still publishes (or still doesn't publish) the
   constraint. Probe rather than read blogs — e.g.
   `curl -s https://graph.facebook.com/v26.0/me` returns an `OAuthException`
   for a version that exists and `Unknown path components` for one that
   doesn't.
2. Check the value is in support, not merely resolving. A deprecated Graph
   version still answers — resolving proves nothing about support.
3. Grep for a second copy. Every entry here has had one at some point.
4. If a merchant hit this constant, loosen it and add a `verify` path instead of
   picking a new number.
