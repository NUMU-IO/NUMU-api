# Meta App Review — Unified FB/IG Inbox

Status: **NOT SUBMITTED** (as of 2026-08-21). This is the long-lead item for
the omnichannel inbox: business verification + review together typically take
2–5 weeks. Start it before any further inbox feature work.

## What we are requesting and why

| Permission | Why we need it | Notes |
| --- | --- | --- |
| `pages_show_list` | List the merchant's Pages in the connect wizard | Usually granted with login |
| `pages_messaging` | Read + reply to Page (Messenger) conversations | **Advanced Access required** |
| `pages_manage_metadata` | `POST /{page-id}/subscribed_apps` to attach our webhook | **Advanced Access required**; without it the subscription call in `ConnectMetaUseCase` fails |
| `pages_read_engagement` | Read the Page node — required for the `instagram_business_account` lookup (IG discovery) | **Advanced Access required**; verified live 2026-08-21: without it even `GET /{page-id}?fields=id,name` 400s |
| `instagram_basic` | Resolve the IG Professional account linked to the Page | |
| `instagram_manage_messages` | Read + reply to Instagram DMs | **Advanced Access required** |

Already-granted WhatsApp permissions (`whatsapp_business_messaging`,
`whatsapp_business_management`) ride on the existing platform WABA
(991122053507329) and are not part of this submission.

**Scope minimization:** `build_authorization_url` currently also asks for
`instagram_manage_insights`, `catalog_management`, and `business_management`.
Every extra permission widens the review and raises rejection risk. Trim the
login scope (or use a separate Facebook Login for Business `config_id`) to
just the inbox set before submitting; add catalog/insights scopes in a later
submission when those features ship.

## Hard prerequisites (blockers)

1. **Business Verification** for the company in Meta Business Manager
   (legal name, registration document, domain/email verification). Advanced
   Access is not grantable without it. Lead time: days to 2 weeks. START FIRST.
2. **Deployed, working webhook endpoint.** Meta's reviewer (and the App
   Dashboard "Verify and save" button) performs the `hub.*` GET handshake
   against `https://numueg.app/api/v1/webhooks/meta`. The fixed route
   (hub.* aliases + raw-body HMAC, 2026-08-21) must be deployed to prod
   first — the previous build 422'd the handshake, so verification was
   impossible.
3. **Prod env vars** on the API EC2: `META_APP_ID`, `META_APP_SECRET`,
   `META_WEBHOOK_VERIFY_TOKEN`, plus the Facebook Login for Business
   `config_id` used by `MetaOAuthService`.
4. **App Dashboard configuration:**
   - Webhooks product → subscribe the app to **Page** and **Instagram**
     objects, fields: `messages`, `messaging_postbacks`, `message_deliveries`,
     `message_reads` (Page) / `messages` (Instagram).
   - Messenger product added; Instagram messaging enabled ("Instagram" product
     → API setup with Instagram login via linked Page).
   - Privacy Policy URL, Terms of Service URL, App Icon, Category — all
     required before switching the app to **Live** mode.
5. **Data Deletion Callback** — required for any app requesting user data.
   Built 2026-08-21 (`src/api/v1/routes/webhooks/meta.py`); configure the
   URLs in App Dashboard → App settings → Basic after deploy:
   - Deauthorize callback: `https://numueg.app/api/v1/webhooks/meta/deauthorize`
   - Data deletion request: `https://numueg.app/api/v1/webhooks/meta/data-deletion`
   Both verify Meta's `signed_request`; data deletion removes matching
   threads/messages and returns the `{url, confirmation_code}` receipt with
   a checkable status endpoint at `/data-deletion/status?code=...`.
6. **Live mode.** Development-mode apps only deliver webhooks for users with
   a role on the app. Review requires Live mode + the screencast below.

## Review submission package

- **Use-case description** (per permission): "NUMU is a multi-tenant
  e-commerce platform for Egyptian/Saudi merchants. Merchants connect their
  own Facebook Page and Instagram Professional account to answer customer
  questions about orders inside one inbox." Emphasize: business-owned assets,
  human agents replying, no automation of unsolicited messages.
- **Screencast** (screen recording, required per permission): full flow —
  hub login → Channels → Connect Facebook & Instagram → Meta consent dialog
  → assets selected → a customer DM arriving in `/inbox` → agent reply
  delivered on Messenger/Instagram. Record against prod or a staging URL
  reachable by the reviewer.
- **Test credentials:** a working hub account on a demo store with a
  connected test Page + IG Professional account, provided in the review notes.
  Reviewers must be able to reproduce the flow themselves.
- **Platform policy pages:** privacy policy must name Meta data usage,
  retention, and the deletion path.

## Known technical gaps the reviewer would hit today

- Human Agent tag / 24-hour window: replies outside the standard messaging
  window fail. v1 should surface "reply window closed" in the composer
  (`HUMAN_AGENT` requires its own separate approval — defer).
- Instagram requires the merchant's account to be **Professional** and
  linked to the Page; the connect wizard should show that fix-it path
  instead of a generic error (per the inbox spec §8).
- Message send path (`/threads/{id}/messages/send`) is untested against a
  live Page — verify before recording the screencast.

## Sequence

1. Start Business Verification (owner action, Business Manager).
2. Deploy webhook fix; set env vars; configure App Dashboard webhooks and
   verify the handshake.
3. Build deauthorize + data-deletion callbacks.
4. Trim login scopes to the inbox set.
5. Connect a real test Page + IG account end-to-end; fix what breaks.
6. Record screencast, write per-permission notes, submit for Advanced Access.
7. While waiting (5–15 business days): nav-gate rollout stays off; continue
   inbox polish behind the gate.
