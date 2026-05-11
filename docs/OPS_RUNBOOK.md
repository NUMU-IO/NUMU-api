# NUMU API — Operations Runbook

Phase 5.8. Status page + incident response. This document is the
on-call's first stop during a SEV1 — links should be one click away
from the alert page.

## Status page

Public status: **https://status.numueg.app** (Statuspage.io —
configured separately; URL ships when DNS lands).

Components surfaced on the public page:
- **API**           — health endpoint backed by `/api/v1/health`
- **Storefront**    — Next.js liveness ping
- **Database**      — `/api/v1/health/detailed` `components.database`
- **Cache**         — same, `components.redis`
- **Background jobs** — same, `components.celery`
- **Email delivery** — Resend webhook tap (TODO: needs separate page)

The status-page provider polls `/api/v1/health` (200 = up) every 60s
and `/api/v1/health/detailed` every 5 minutes for component-level
status. Polls from Statuspage.io's known IPs are rate-limit-exempt
(see `rate_limit.SKIP_RATE_LIMIT`).

## Severity definitions

| Sev | Trigger | Response time |
|---|---|---|
| **SEV1** | Storefront down, checkout broken, data loss | 15 min — page on-call |
| **SEV2** | Degraded perf, single-merchant outage, payment-provider issue | 1 hr |
| **SEV3** | Background job stuck, non-critical email failures | next business day |
| **SEV4** | Cosmetic, localization gaps | backlog |

## On-call rotation

Primary: dev team rotation (week-long shifts, handoff Mondays 09:00
Cairo). Secondary: lead engineer (always).

Paging:
- **PagerDuty integration key** — TBD (env `PAGERDUTY_INTEGRATION_KEY`)
- Sentry alerts trigger directly to PagerDuty for SEV1 patterns
  (5xx error rate >1%, p95 latency >2s, DB unreachable)

## Common scenarios

### Storefront returns 502 / 503

1. Check `/api/v1/health` — if unreachable, the API is down.
2. Check the api droplet (`ssh root@188.166.156.151 -i ~/.ssh/numu_deploy`).
3. `docker compose -f /opt/numu/docker/docker-compose.staging.yml ps`
   — look for unhealthy containers.
4. `docker logs numu-api-staging --tail 200` for stack traces.
5. Common causes: OOM (DB pool saturation under load), Redis cluster
   eviction, Sentry self-report failure cascading.

### Celery worker not processing

1. `/api/v1/health/detailed` → `components.celery.workers_responding`
   — if 0, no worker is alive.
2. `docker logs numu-celery-staging --tail 100` for crash traces.
3. Beat-stuck symptom: `components.celery.beat_last_seen_seconds_ago`
   > 120s. The beat container restarted but didn't reconnect to the
   broker. Restart: `docker compose restart numu-celery-beat`.
4. Tasks piling up but completing eventually = backlog, not outage.
   Inspect with `celery -A src.infrastructure.messaging.celery_app
   inspect active`.

### DLQ filling up

1. Hub → Settings → Background Jobs → Dead-Letter Queue
   (will ship with the merchant-hub follow-up). Until that UI
   lands, query `SELECT task_name, count(*) FROM celery_dead_letters
   WHERE status = 'pending' GROUP BY task_name`.
2. Inspect the most-recent `last_error` for each task name to
   identify root cause. Common culprits:
   - Resend rate-limited (transient — bulk retry usually works)
   - Paymob/Fawry callback signature mismatch (configuration; needs
     merchant fix)
3. After root cause is fixed, replay via the hub UI or directly:
   `await replay_dead_letter(<entry_id>)` from a Python shell on
   the API container.

### ETA e-Invoice rejections spiking

1. Hub → Invoices → filter by status=rejected.
2. Check `eta_status_message` on rejected rows for the ETA error
   code. Common codes:
   - `4001` — invalid tax ID format on the merchant's seller info
   - `4101` — line-item code not in ETA's catalog (merchant must
     add the GS1 code)
3. If outage on ETA's side: their portal at https://invoicing.eta.gov.eg/
   shows incidents. Pause submissions by setting
   `ETA_ENABLED=false` in env; the simulated path takes over and
   merchants can re-submit later.

### Refresh-token reuse alerts

`refresh_token_reuse_detected` warnings from the security log are
**always** investigated within 1hr. Common false-positives: a user
on a flaky network where the same cookie hits the server twice in
parallel due to retry-on-timeout. Real positives: stolen device,
phishing.

When you see one:
1. Pull the user's recent session log (Sentry breadcrumbs +
   `email_logs` table — show the last 24h).
2. If they reset their password recently from a different IP/UA,
   we have the email log to confirm.
3. Force-logout via the admin "revoke all sessions for user" tool
   (TODO: ships with 5.x admin work).

## Load testing

`/specs/ops-load-tests/` (Phase 5.9). Run on staging weekly:

```
k6 run -e BASE_URL=https://staging.numueg.app scripts/storefront_load.js
```

Pass criteria: p95 < 500ms, no 5xx, 100 concurrent shoppers for
10 minutes.

## Backups + restore

Daily database backups → Cloudflare R2 bucket
`numu-db-backups/{date}.sql.gz`. Retention 30 days hot, 1 year
glacier.

To restore from a backup:

```
# 1. Pull the dump
docker exec numu-api-staging python -m src.infrastructure.tasks.restore_backup --date 2026-05-08

# 2. Verify before taking over: dry-run with --check, then --apply.
```

## Communication

During SEV1:
1. **Update status.numueg.app** within 5 minutes of confirming the
   incident. Post incidents at https://manage.statuspage.io.
2. **Slack #incidents** — `/incident SEV1 storefront down` triggers
   the bot to post to the channel + DM the on-call.
3. **Tweet** for SEV1s lasting >30 minutes (account @numueg_status).

Post-incident:
- 24-hour postmortem doc using the template at
  `docs/incident-postmortem-template.md`.
- Link the postmortem from the resolved Statuspage incident.
