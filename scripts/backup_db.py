"""Database backup: pg_dump compressed → upload to Cloudflare R2.

Usage:
    python scripts/backup_db.py              # one-off backup
    python scripts/backup_db.py --prune      # backup + delete old backups beyond retention

Environment variables (loaded from .env via Settings):
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
    R2_BACKUP_BUCKET_NAME (default: numu-db-backups)
    BACKUP_RETENTION_DAYS (default: 30)
"""

from __future__ import annotations

import gzip
import logging
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so `src.config` is importable.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.config.settings import Settings  # noqa: E402


def _get_settings() -> Settings:
    """Load settings without hitting the module-level cached singleton."""
    return Settings()


def _build_r2_client(settings: Settings):
    """Build a boto3 S3 client pointed at Cloudflare R2."""
    if not all([
        settings.r2_account_id,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
    ]):
        raise RuntimeError(
            "R2 credentials are not configured. "
            "Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, and R2_SECRET_ACCESS_KEY."
        )
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
    )


def run_pg_dump(settings: Settings, output_path: Path) -> None:
    """Run pg_dump and write a gzip-compressed SQL file to *output_path*."""
    env = {
        "PGPASSWORD": settings.postgres_password,
    }
    cmd = [
        "pg_dump",
        "-h",
        settings.postgres_host,
        "-p",
        str(settings.postgres_port),
        "-U",
        settings.postgres_user,
        "-d",
        settings.postgres_db,
        "--no-owner",
        "--no-acl",
        "--format=plain",
    ]

    logger.info("Running pg_dump for database '%s' …", settings.postgres_db)
    result = subprocess.run(
        cmd,
        env={**dict(__import__("os").environ), **env},
        capture_output=True,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {stderr}")

    with gzip.open(output_path, "wb") as f:
        f.write(result.stdout)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("Dump compressed to %.2f MB → %s", size_mb, output_path)


def upload_to_r2(settings: Settings, local_path: Path, object_key: str) -> str:
    """Upload *local_path* to R2 and return the object key."""
    client = _build_r2_client(settings)
    bucket = settings.r2_backup_bucket_name

    logger.info("Uploading to R2 bucket '%s' key '%s' …", bucket, object_key)
    client.upload_file(
        str(local_path),
        bucket,
        object_key,
        ExtraArgs={"ContentType": "application/gzip"},
    )
    logger.info("Upload complete.")
    return object_key


def prune_old_backups(settings: Settings) -> int:
    """Delete backups older than retention period. Returns count deleted."""
    client = _build_r2_client(settings)
    bucket = settings.r2_backup_bucket_name
    retention_days = settings.backup_retention_days
    cutoff = datetime.now(UTC).timestamp() - (retention_days * 86400)

    paginator = client.get_paginator("list_objects_v2")
    deleted = 0

    for page in paginator.paginate(Bucket=bucket, Prefix="backups/"):
        for obj in page.get("Contents", []):
            last_modified = obj["LastModified"].timestamp()
            if last_modified < cutoff:
                client.delete_object(Bucket=bucket, Key=obj["Key"])
                logger.info("Pruned old backup: %s", obj["Key"])
                deleted += 1

    logger.info("Pruned %d backup(s) older than %d days.", deleted, retention_days)
    return deleted


def create_backup(prune: bool = False) -> str:
    """Run a full backup cycle. Returns the R2 object key.

    This is the main entry point used by both the CLI and the Celery task.
    """
    settings = _get_settings()
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"{settings.postgres_db}_{timestamp}.sql.gz"
    object_key = f"backups/{filename}"

    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / filename
        run_pg_dump(settings, dump_path)
        upload_to_r2(settings, dump_path, object_key)

    if prune:
        prune_old_backups(settings)

    logger.info("Backup complete: %s", object_key)
    return object_key


def main() -> None:
    prune = "--prune" in sys.argv
    try:
        key = create_backup(prune=prune)
        print(f"Backup saved to: {key}")
    except Exception:
        logger.exception("Backup failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
