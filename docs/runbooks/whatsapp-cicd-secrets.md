# WhatsApp CI/CD secrets — per-environment setup

The CD workflow (`cd.yml`) now syncs WhatsApp + Meta secrets from
GitHub Actions secrets into the on-droplet `.env.*` files **on every
deploy**. The sync is **idempotent** — empty secrets are skipped (no
overwrite), set secrets are upserted (replace if present, append if
absent).

This runbook documents the per-environment secret naming so the
right values land in the right file.

## Secret naming convention

GitHub Actions doesn't support per-environment secrets at the repo
level (only at the environment level, which the project doesn't use).
We disambiguate via a `<ENV>_` prefix that matches the project memory
convention (`feedback_cd_environment_scoping`):

| Droplet env file | Workflow job | Secret prefix |
|---|---|---|
| `/opt/numu/.env.test` | `deploy-test` | `TEST_` |
| `/opt/numu/.env.stage` | `deploy-stage` | `STAGE_` |
| `/opt/numu/.env.staging` (prod) | `deploy-apex` | `STAGING_` |

**Production uses `STAGING_*` not `STAGE_*`** — historical naming;
documented in the project memory.

## Required secrets (per environment)

Set each of these in GitHub → Settings → Secrets and variables →
Actions → Repository secrets, replacing `<ENV>` with one of `TEST`,
`STAGE`, `STAGING`:

| Secret name | Value | Required for |
|---|---|---|
| `<ENV>_WHATSAPP_ACCESS_TOKEN` | Meta System User access token | Sending messages from platform-managed stores |
| `<ENV>_WHATSAPP_PHONE_NUMBER_ID` | Numeric phone-number id from Meta | Sending |
| `<ENV>_WHATSAPP_BUSINESS_ACCOUNT_ID` | NUMU's WABA id | T093 boot-time webhook subscription + polling sync |
| `<ENV>_WHATSAPP_WEBHOOK_VERIFY_TOKEN` | Free-form string, matches what's set at Meta's webhook config | GET /webhooks/whatsapp/callback challenge |
| `<ENV>_WHATSAPP_APP_SECRET` | Meta app secret | Webhook signature verification (HMAC-SHA256) |
| `<ENV>_WHATSAPP_ENABLED` | `true` to enable sends; default `false` | Master switch |
| `<ENV>_META_APP_ID` | Meta app id | Platform-app boot subscription (T093) + embedded signup |

> `WHATSAPP_BUSINESS_API_VERSION` is wired as a workflow-level constant
> (`v21.0`) since it doesn't carry secret material and changes rarely.
> Bump the value in `.github/workflows/cd.yml` to upgrade.

## What the sync step does

For each deploy job (`deploy-test`, `deploy-stage`, `deploy-apex`),
right after the SSH key is installed and BEFORE `docker compose up`,
the workflow:

1. Reads each `<ENV>_WHATSAPP_*` + `<ENV>_META_APP_ID` from secrets.
2. Builds a `KEY=value\n` snippet locally, skipping empty secrets.
3. If the snippet is empty, exits early (env was never configured —
   the existing manual entries in the .env file are left alone).
4. Otherwise SSHes to the droplet, pipes the snippet over stdin, and
   for each line:
   - `grep -v "^KEY="` removes any existing line with that key.
   - Appends the new `KEY=value` line.
   - Atomic rename of the tempfile.
5. Logs `upserted KEY in $FILE` for each key that was written.

The operation is **safe to re-run** — running the same workflow twice
produces the same final state.

## Why upsert (not replace-the-whole-file)

Each `.env.*` file also contains keys we DO NOT manage from secrets
(database URLs, S3 creds, Sentry DSN, etc.). A full-file replace
would blow them away. Upserting only the WhatsApp/Meta keys means:
- Other env vars remain under manual control on the droplet.
- Adding a new WhatsApp key in the future = add it to the workflow's
  `env:` block, then deploy.
- A leaked GitHub secret only affects the WhatsApp keys it controls,
  not the rest of the env file.

## First-time setup

1. In Meta Business Manager:
   - Create / confirm the System User access token with
     `whatsapp_business_management` + `whatsapp_business_messaging`
     scopes.
   - Note the phone-number id, WABA id, and app secret.
2. Choose a webhook verify token (any random secret string).
3. Configure Meta's webhook URL: `https://<env>.numueg.app/api/v1/webhooks/whatsapp/callback`
   with that verify token.
4. Set all 7 secrets in GitHub for the env you're configuring.
5. Push to the appropriate branch:
   - `dev` → test
   - `stage` → stage
   - `prod` → apex (production)
6. Watch the CD log for `[whatsapp-env] upserted <KEY> in <FILE>`
   lines — that's the sync confirming each key landed.
7. After deploy completes, SSH into the droplet and verify:
   ```sh
   grep -E '^(WHATSAPP|META_APP_ID)' /opt/numu/.env.test
   ```
8. Run the smoke test from the droplet:
   ```sh
   docker compose -p numu-test --env-file /opt/numu/.env.test \
     -f docker/docker-compose.test.yml exec api \
     python scripts/whatsapp_smoke_test.py
   ```

## Backfilling existing envs

For envs that already have manual WhatsApp config in `.env.*`:
- The sync step OVERWRITES keys it manages whenever the secret is set
- Set the secrets to match the current manual values first to avoid
  a "deploy unexpectedly changed prod creds" surprise
- Or: leave the secret unset for keys you want to keep manual control of

## What to do when a token rotates

Meta access tokens expire (typically every ~60 days for non-system-user
tokens, never for system-user tokens with no expiry). When you rotate:
1. Update the `<ENV>_WHATSAPP_ACCESS_TOKEN` secret in GitHub.
2. Re-deploy (push a no-op commit, or use the workflow's manual
   `workflow_dispatch` trigger).
3. The sync step upserts the new token; next send picks it up because
   the messaging service reads `settings.whatsapp_access_token` on
   each request through the resolver.
