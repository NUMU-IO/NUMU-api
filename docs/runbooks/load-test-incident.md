# Storefront performance incident runbook

You are reading this because an alert from `docker/monitoring/prometheus/alerts/storefront-perf.yml` fired. Each section below corresponds to one alert. **Read only your section.** Cap each section at 8 numbered steps; if you need more, the underlying problem isn't sharp enough — escalate.

Open in order:

1. The **Storefront — Performance** Grafana dashboard (`uid: storefront-perf`).
2. Sentry, filtered to `environment:production` (or `staging` if that's where the alert fired) and **last 30 minutes**.
3. This runbook section.

If two alerts are firing simultaneously, work them in this order: **SwapPressure → 5xx rate → DBPoolWaitHigh / DBPoolAtCapacity → P95Latency → CacheHitRateLow → ReplicationLag**. Memory + 5xx win because they cascade into the others.

---

## High p95 latency

*Alert:* `StorefrontP95Latency` (p95 across storefront routes > 1s for 10 min)

1. Dashboard → Row 2 — *Top 10 slowest routes (p95, 5m)*. Note the top three offenders.
2. For each, check Row 4 — *Cache hit rate by layer*. If `storefront` < 60 %, jump to **Cache hit rate low** below.
3. Row 3 — *DB pool wait p95*. If > 100 ms, jump to **DB pool wait high**.
4. Sentry → search for the slow routes. Recent regression? Recent deploy? `git log --since='4 hours ago'`.
5. If a single route dominates, look at its handler — has it grown a new awaited call? Is there a new external API in the path (Meta CAPI, geocode)?
6. If the regression correlates with the last deploy, roll back: `make staging-rollback` (or the prod equivalent).
7. Still unclear after step 6? Page `@on-call-secondary`. Include the dashboard URL with the time range pinned.
8. Update the alert thresholds only AFTER root cause is known — never to silence the alert.

---

## 5xx rate spike

*Alert:* `StorefrontErrorRate` (storefront 5xx rate > 1 % for 5 min)

1. Sentry → group by error type, last 30 min. **Note the top stack trace.**
2. Common buckets:
   - `RedisError` / `ConnectionError` to Redis → `docker compose ps redis-master`; if down, restart.
   - `OperationalError` / `InterfaceError` from asyncpg → Postgres up? `docker compose logs db | tail -50`. If recovering, this self-heals; if not, escalate to DBA on-call.
   - `HTTPStatusError` from a third party (Paymob / Fawry / Bosta / Meta) → check their status page; consider the upstream-down circuit-breaker (Sentry filter `provider:<name>`).
   - `ValidationError` 4xx leaking as 5xx → likely a deploy regression. Roll back.
3. If errors cluster on one route, treat it as a deploy regression first — `git log src/ src/api/v1/routes/ --since='2 hours ago'` and roll back the most recent that touches the implicated module.
4. If a third party is implicated, post in `#numu-vendors` with the relevant 5-minute window and your incident channel.
5. Page `@on-call-secondary` if 5xx rate is sustained > 5 % for 2 min OR if `revenue_processing` impact is observed.
6. After mitigation: file a Sentry incident task and write a 1-paragraph post-mortem.

---

## DB pool wait high / pool at capacity

*Alerts:* `DBPoolWaitHigh` (wait p95 > 100 ms for 5 min) / `DBPoolAtCapacity` (in_use ≥ size + overflow for 2 min)

1. Dashboard Row 3 — *DB connections*. Confirm `in_use` is pinned at `pool_size + overflow`.
2. `SELECT pid, state, query_start, NOW() - query_start AS age, left(query, 80) FROM pg_stat_activity WHERE state != 'idle' ORDER BY age DESC LIMIT 20;`
3. Long-running transactions (age > 30 s) on storefront tables? Cancel cautiously: `SELECT pg_cancel_backend(pid);`. Never `pg_terminate_backend` unless the transaction is clearly orphaned.
4. Single endpoint holding connections (Sentry → "slow transactions" filter)? Roll back the latest deploy that touched it.
5. If PgBouncer is in front (post Step 10), `SHOW POOLS` and check `cl_waiting > 0`. Adjust `default_pool_size` only after confirming with platform lead.
6. Short-term mitigation: bump `pool_size` in `src/config/settings.py` and redeploy. Long-term: revisit Step 10 / Step 14.

---

## Swap pressure

*Alert:* `SwapPressure` (swap free < 30 % for 2 min)

1. SSH the droplet. `vmstat 1 5` — confirm sustained swap-in (`si` > 0).
2. `docker stats --no-stream` → which container is using the most memory?
3. If it's `celery-worker` — `docker compose restart celery-worker`. Workers leak occasionally; restart is fine. Re-check in 5 min.
4. If it's `api` — investigate **before** restarting. Sentry → memory-related errors? Recent deploy added a fat in-memory cache?
5. If it's Postgres — usually a runaway query. Combine with **DB pool wait high** above.
6. If multiple containers are co-tenanting the droplet, this is Step 07's territory — note in the post-mortem and follow up with infra.

---

## Cache hit rate low

*Alert:* `CacheHitRateLow` (storefront hit rate < 60 % for 15 min)

1. `docker compose ps redis-master` — running?
2. If we deployed in the last 5–10 min, expect a cold-cache window. Wait. The alert auto-resolves once warm.
3. Recent change to invalidation? `git log src/infrastructure/cache/ src/api/v1/routes/stores/ --since='2 hours ago'`. Look for invalidate calls in unexpected places.
4. Massive merchant bulk-edit? Check the merchant hub audit log — a single store doing 5,000 product updates triggers product-cache invalidation cascades.
5. Redis memory pressure → eviction? `redis-cli INFO memory` (or via the Grafana cache row → Redis-memory panel). If `maxmemory_policy` is evicting hot keys, raise `maxmemory` and redeploy.
6. If the layer dropping is `promotion`, check `numu_visitor` cookie distribution — a sudden surge of unique visitors looks like cache misses but is correct behaviour. Cross-check Row 1's RPS panel; if RPS is unusually high, this is a traffic spike, not a cache regression.

---

## Replication lag

*Alert:* `ReplicationLagHigh` (replica > 30 s behind primary for 5 min). Only fires post-Step-13.

1. DO Managed Postgres dashboard → replica lag graph. Confirm via the cloud console (the alert just mirrors that metric).
2. Often correlated with a long transaction on primary (bulk import, migration). `pg_stat_activity` on primary as in **DB pool wait high** step 2.
3. If the replica is critical for the current workload (heavy read traffic), flip the kill switch: set `REPLICA_ENABLED=false` and redeploy. All reads go to primary. Latency on primary will rise; that's intentional during recovery.
4. Investigate primary load before re-enabling. If a backfill is running, let it finish or split into smaller batches.
5. Re-enable replica reads after lag < 5 s for 10 min.

---

## After every incident

- Update this runbook if a step was wrong or missing. Cap new sections at 8 steps.
- Post a one-paragraph post-mortem in `#numu-platform-incidents`. Cause + mitigation + follow-up issue link.
- File a follow-up issue tagged `perf` if the underlying fix is non-trivial.
