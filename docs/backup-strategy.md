# NUMU Database Backup Strategy

## Overview

NUMU uses a two-layer backup strategy: automated Celery Beat tasks for scheduled
backups and a standalone shell script for manual/cron-based backups. All backups
are compressed pg_dump files uploaded to Cloudflare R2.

---

## Retention Policy

| Frequency | Retention   | Storage Path         |
|-----------|-------------|----------------------|
| Daily     | 30 days     | `r2://numu-db-backups/daily/` |
| Weekly    | 52 weeks (1 year) | `r2://numu-db-backups/weekly/` |
| Manual    | Until deleted | `r2://numu-db-backups/manual/` |

Weekly backups are Sunday's daily backup promoted to the weekly prefix.
Promotion is handled by the Celery Beat `weekly-backup-promotion` task.

---

## Automated Backups (Celery Beat)

Configured in `src/infrastructure/messaging/celery_app.py`:

```python
"daily-database-backup": {
    "task": "tasks.backup_database",
    "schedule": crontab(hour=3, minute=0),   # 03:00 UTC daily
}
```

The task calls `scripts/backup_db.py:create_backup(prune=True)` which:
1. Runs `pg_dump` against the primary database
2. Compresses the output with gzip (level 9)
3. Uploads to Cloudflare R2 under `daily/numu_<YYYYMMDD_HHMMSS>.sql.gz`
4. Deletes R2 objects older than `BACKUP_RETENTION_DAYS` (default 30)

**Worker requirement:** The Celery worker must have `pg_dump` and `gzip` on PATH
and R2 credentials in the environment.

---

## Manual Backup via Shell Script

`scripts/backup.sh` can be run directly on any host with database access:

```bash
# Full backup + prune old local files
./scripts/backup.sh

# Backup without pruning local files
./scripts/backup.sh --no-prune

# Restore from a backup file
./scripts/backup.sh --restore /path/to/numu_20260101_030000.sql.gz
```

### Cron setup (server)

```cron
# Daily at 03:00 UTC (backup + prune)
0 3 * * * /opt/numu/scripts/backup.sh >> /var/log/numu/backup.log 2>&1
```

---

## Required Environment Variables

| Variable                  | Description                              | Default            |
|---------------------------|------------------------------------------|--------------------|
| `POSTGRES_HOST`           | Database host                            | `localhost`        |
| `POSTGRES_PORT`           | Database port                            | `5432`             |
| `POSTGRES_USER`           | Database user                            | `postgres`         |
| `POSTGRES_PASSWORD`       | Database password                        | —                  |
| `POSTGRES_DB`             | Database name                            | `numu`             |
| `R2_BACKUP_BUCKET_NAME`   | R2 bucket for backup storage             | `numu-db-backups`  |
| `BACKUP_RETENTION_DAYS`   | Days to retain daily backups             | `30`               |
| `AWS_ACCESS_KEY_ID`       | R2 access key (mapped from `R2_ACCESS_KEY_ID`) | —         |
| `AWS_SECRET_ACCESS_KEY`   | R2 secret key                            | —                  |
| `AWS_ENDPOINT_URL`        | R2 endpoint (e.g. `https://<account>.r2.cloudflarestorage.com`) | — |

---

## Restore Procedure

### 1. Download the backup from R2

```bash
aws s3 cp s3://numu-db-backups/daily/numu_20260101_030000.sql.gz /tmp/ \
    --endpoint-url https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

### 2. Stop the application

```bash
docker compose -f docker/docker-compose.prod.yml stop api celery-worker celery-beat
```

### 3. Restore via the script

```bash
./scripts/backup.sh --restore /tmp/numu_20260101_030000.sql.gz
```

Or manually with psql:

```bash
gunzip -c /tmp/numu_20260101_030000.sql.gz \
    | PGPASSWORD=$POSTGRES_PASSWORD psql \
        -h $POSTGRES_HOST -U $POSTGRES_USER -d $POSTGRES_DB
```

### 4. Re-run migrations to ensure schema is current

```bash
docker compose -f docker/docker-compose.prod.yml run --rm api alembic upgrade head
```

### 5. Restart services

```bash
docker compose -f docker/docker-compose.prod.yml up -d
```

### 6. Verify

```bash
curl -f https://api.numu.com/api/v1/health
```

---

## Monitoring

- **Celery Beat failures** are reported to the Sentry `infra` channel and the
  `#infra` Slack channel if `SLACK_ENABLED=true`.
- Local backup logs are written to stdout; redirect to a file in cron (see above).
- R2 storage usage can be monitored in the Cloudflare dashboard.

---

## Testing the Restore Procedure

Restore drills should be performed:
- **Quarterly** against a staging database clone
- **After any schema migration** that drops columns or tables
- **Before major releases**

Staging restore test:

```bash
# On staging server
./scripts/backup.sh --restore /path/to/latest_backup.sql.gz
# Verify app health
curl http://localhost:8021/api/v1/health
```
