# NUMU Beta Launch Runbook

**Target:** 5-10 beta merchants | **Window:** 2026-02-10 to 2026-03-10

---

## 1. Pre-Launch Checklist (T-3 days)

### Infrastructure
- [ ] PostgreSQL RDS running, backups enabled (daily snapshots)
- [ ] Redis instance healthy (Celery broker + cache)
- [ ] Docker images built and pushed to ECR/registry
- [ ] Domain DNS configured: `*.numu.io` wildcard CNAME
- [ ] TLS certificates issued (wildcard `*.numu.io` + `numu.io`)
- [ ] Cloudflare R2 bucket created with CORS policy
- [ ] Sentry project created, DSN configured in `.env`

### Application
- [ ] Run Alembic migrations: `alembic upgrade head`
- [ ] Verify new tables exist: `waitlist`, `feedback`
- [ ] Verify enum types created: `waitliststatus`, `feedbackcategory`
- [ ] Set `BETA_MODE=true` in environment
- [ ] Set `ETA_ENABLED=false` (ETA submission disabled for beta)
- [ ] Verify Resend API key configured for email
- [ ] Run seed script: `python -m scripts.seed_beta_merchants --dry-run` then without `--dry-run`
- [ ] Create super admin account: `python -m scripts.create_superuser`
- [ ] Smoke test: health endpoint returns 200
- [ ] Smoke test: `/api/v1/public/stats` returns counts
- [ ] Smoke test: `/api/v1/public/features` returns feature list

### Payment Gateways (Staging)
- [ ] Paymob staging credentials configured
- [ ] Paymob webhook URL registered: `https://api.numu.io/api/v1/webhooks/paymob`
- [ ] Fawry staging credentials configured (if enabled)
- [ ] Test payment flow end-to-end with card and wallet

### Monitoring
- [ ] Sentry alerts configured (P1: Slack #numu-alerts)
- [ ] Database connection pool monitoring
- [ ] API response time baseline captured (p50 < 200ms, p99 < 1s)
- [ ] Disk space alert at 80%
- [ ] Rate limiter configured: 60 req/min general, 5 req/min auth

---

## 2. Launch Day (T=0)

### Sequence
1. **08:00** — Final migration check: `alembic current` matches head
2. **08:30** — Deploy production containers
3. **09:00** — Verify health: `curl https://api.numu.io/api/v1/health`
4. **09:15** — Send beta invites via admin panel:
   - `POST /api/v1/admin/waitlist/invite` for each selected merchant
   - Verify email delivery in Resend dashboard
5. **09:30** — Monitor first merchant signups in real-time
6. **10:00** — Verify first store creation completes successfully
7. **10:30** — Test order flow on first live store
8. **11:00** — Status update to team on Slack #numu-beta

### Quick Verification Commands
```bash
# Check all services healthy
curl -s https://api.numu.io/api/v1/health/detailed | jq .

# Count waitlist entries
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://api.numu.io/api/v1/admin/waitlist?status=invited | jq .data.total

# Check stores created
psql $DATABASE_URL -c "SELECT count(*) FROM public.stores WHERE status='active';"

# Check for errors in last hour
# (Sentry dashboard: https://sentry.io/organizations/numu/)
```

---

## 3. Rollback Plan

### Severity Levels

| Level | Condition | Action |
|-------|-----------|--------|
| **P0** | Data corruption, payment failures, security breach | Immediate rollback |
| **P1** | Store creation broken, checkout broken | Rollback within 30 min |
| **P2** | Non-critical feature broken (analytics, feedback) | Hotfix within 4 hours |
| **P3** | UI glitch, minor inconsistency | Fix in next deploy |

### Rollback Steps (P0/P1)

```bash
# 1. Switch to maintenance mode
export MAINTENANCE_MODE=true
# Restart containers to pick up env change

# 2. Roll back to previous container image
docker-compose down
docker-compose -f docker-compose.yml up -d --force-recreate

# 3. If DB migration caused the issue, roll back migration
alembic downgrade -1

# 4. Notify merchants
# Send email via Resend: "Temporary maintenance — back within 1 hour"

# 5. Investigate root cause
# Check logs: docker logs numu-api --tail 500
# Check Sentry for error details
```

### Database Rollback (Nuclear Option)
```bash
# Restore from most recent snapshot (< 24h old)
# WARNING: This loses all data since snapshot

# 1. Stop application
docker-compose down

# 2. Restore PostgreSQL snapshot
pg_restore -h $PG_HOST -U $PG_USER -d numu_restored latest_backup.dump

# 3. Swap databases
psql -c "ALTER DATABASE numu RENAME TO numu_broken;"
psql -c "ALTER DATABASE numu_restored RENAME TO numu;"

# 4. Restart application
docker-compose up -d
```

---

## 4. Monitoring Alerts

### Automated Alerts (Slack #numu-alerts)

| Alert | Threshold | Channel |
|-------|-----------|---------|
| API Error Rate > 5% | 5 min window | #numu-alerts |
| Response Time p99 > 3s | 5 min window | #numu-alerts |
| Database Connection Pool > 80% | Instant | #numu-alerts |
| Payment Webhook Failure | Each occurrence | #numu-payments |
| Failed Store Creation | Each occurrence | #numu-alerts |
| Disk Space > 80% | 15 min check | #numu-infra |

### Daily Checks (First 2 Weeks)
- [ ] Review Sentry error dashboard (target: 0 unresolved P1/P2)
- [ ] Check merchant store count and order volume
- [ ] Review feedback submissions: `/api/v1/admin/feedback`
- [ ] Check waitlist conversion rate
- [ ] Verify daily database backup completed
- [ ] Review API response time trends

---

## 5. Support Escalation

### Tiers

| Tier | Response Time | Who | Contact |
|------|--------------|-----|---------|
| **L1** | < 1 hour | On-call engineer | Slack #numu-support |
| **L2** | < 4 hours | Backend lead | Direct message |
| **L3** | < 24 hours | CTO / Principal | Phone call |

### Common Beta Issues

| Issue | Quick Fix |
|-------|-----------|
| Merchant can't create store | Check invite code status in waitlist table |
| Payment not completing | Check Paymob webhook logs, verify HMAC key |
| Email not arriving | Check Resend dashboard, verify API key |
| Store subdomain not resolving | Check DNS wildcard, verify TenantMiddleware |
| Slow page loads | Check DB query times, Redis connectivity |
| Invoice PDF not generating | WeasyPrint needs system libs (pango, cairo) |

### Merchant Communication Templates

**Welcome (Day 1):**
> Thanks for joining the NUMU beta! Your store is live at {subdomain}.numu.io.
> Reply to this email or use the in-app feedback button for any issues.

**Check-in (Day 7):**
> How's your first week on NUMU? We'd love your feedback —
> use the feedback form in your dashboard or reply to this email.

**Issue Acknowledgment:**
> We've received your report about {issue}. Our team is investigating
> and we'll update you within {timeline}. Thank you for your patience.

---

## 6. Post-Launch Review (T+7 days)

- [ ] Compile merchant feedback summary from `/api/v1/admin/feedback`
- [ ] Calculate conversion rate: waitlist invites -> active stores
- [ ] Identify top 3 pain points from feedback categories
- [ ] Review error budget: total errors / total requests
- [ ] Plan hotfix priorities for Week 2
- [ ] Decide: expand beta (invite more) or hold for fixes

---

## 7. Beta Exit Criteria

Before general availability, all of the following must be met:

- [ ] 0 unresolved P0/P1 bugs
- [ ] Average merchant feedback rating >= 3.5/5
- [ ] Payment success rate >= 98%
- [ ] API uptime >= 99.5% over 30 days
- [ ] At least 5 merchants with real orders processed
- [ ] ETA e-invoicing tested with Tax Authority sandbox
- [ ] Load test: 100 concurrent users, p99 < 2s
- [ ] Security audit completed (OWASP top 10)
- [ ] Set `BETA_MODE=false` to remove invite code requirement
