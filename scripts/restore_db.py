"""Database restore: download backup from R2 → decompress → psql.

Usage:
    python scripts/restore_db.py                            # restore latest backup
    python scripts/restore_db.py backups/numu_20260201_030000.sql.gz   # restore specific key
    python scripts/restore_db.py --list                     # list available backups

Environment variables: same as backup_db.py.

WARNING: This drops and recreates the target database. Use with care.
"""

from __future__ import annotations

import gzip
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

import boto3
from botocore.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.config.settings import Settings  # noqa: E402


def _get_settings() -> Settings:
    return Settings()


def _build_r2_client(settings: Settings):
    if not all([
        settings.r2_account_id,
        settings.r2_access_key_id,
        settings.r2_secret_access_key,
    ]):
        raise RuntimeError("R2 credentials are not configured.")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        config=Config(signature_version="s3v4"),
    )


def list_backups(settings: Settings) -> list[dict]:
    """List available backups from R2, newest first."""
    client = _build_r2_client(settings)
    bucket = settings.r2_backup_bucket_name
    paginator = client.get_paginator("list_objects_v2")

    backups = []
    for page in paginator.paginate(Bucket=bucket, Prefix="backups/"):
        for obj in page.get("Contents", []):
            backups.append({
                "key": obj["Key"],
                "last_modified": obj["LastModified"],
                "size_mb": round(obj["Size"] / (1024 * 1024), 2),
            })

    backups.sort(key=lambda b: b["last_modified"], reverse=True)
    return backups


def download_from_r2(settings: Settings, object_key: str, dest: Path) -> Path:
    """Download a backup file from R2."""
    client = _build_r2_client(settings)
    bucket = settings.r2_backup_bucket_name

    logger.info("Downloading '%s' from bucket '%s' …", object_key, bucket)
    client.download_file(bucket, object_key, str(dest))
    logger.info("Downloaded %.2f MB → %s", dest.stat().st_size / (1024 * 1024), dest)
    return dest


def restore_database(settings: Settings, sql_path: Path) -> None:
    """Drop + recreate the database and load the SQL dump via psql."""
    env = {
        **dict(__import__("os").environ),
        "PGPASSWORD": settings.postgres_password,
    }
    pg_args = [
        "-h",
        settings.postgres_host,
        "-p",
        str(settings.postgres_port),
        "-U",
        settings.postgres_user,
    ]
    db = settings.postgres_db

    # Terminate existing connections and drop/recreate the database.
    terminate_sql = (
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{db}' AND pid <> pg_backend_pid();"
    )
    for sql in [
        terminate_sql,
        f"DROP DATABASE IF EXISTS {db};",
        f"CREATE DATABASE {db};",
    ]:
        subprocess.run(
            ["psql", *pg_args, "-d", "postgres", "-c", sql],
            env=env,
            check=True,
            capture_output=True,
        )
    logger.info("Database '%s' recreated.", db)

    # Load the dump.
    logger.info("Restoring from %s …", sql_path)
    result = subprocess.run(
        ["psql", *pg_args, "-d", db, "-f", str(sql_path)],
        env=env,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(f"psql restore failed (exit {result.returncode}): {stderr}")

    logger.info("Restore complete.")


def restore_backup(object_key: str | None = None) -> None:
    """Download a backup from R2 and restore it.

    If *object_key* is None, the latest backup is used.
    """
    settings = _get_settings()

    if object_key is None:
        backups = list_backups(settings)
        if not backups:
            raise RuntimeError("No backups found in R2.")
        object_key = backups[0]["key"]
        logger.info("Using latest backup: %s", object_key)

    with tempfile.TemporaryDirectory() as tmp:
        gz_path = Path(tmp) / "backup.sql.gz"
        download_from_r2(settings, object_key, gz_path)

        sql_path = Path(tmp) / "backup.sql"
        logger.info("Decompressing …")
        with gzip.open(gz_path, "rb") as f_in, open(sql_path, "wb") as f_out:
            while chunk := f_in.read(1024 * 1024):
                f_out.write(chunk)

        restore_database(settings, sql_path)


def main() -> None:
    if "--list" in sys.argv:
        settings = _get_settings()
        backups = list_backups(settings)
        if not backups:
            print("No backups found.")
            return
        print(f"{'Key':<60} {'Date':<25} {'Size (MB)':>10}")
        print("-" * 97)
        for b in backups:
            print(f"{b['key']:<60} {str(b['last_modified']):<25} {b['size_mb']:>10}")
        return

    # Positional arg = specific object key, otherwise latest.
    key = None
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            key = arg
            break

    confirm = input(
        f"This will DROP and recreate the database. "
        f"Restoring: {'latest backup' if key is None else key}\n"
        f"Type 'yes' to continue: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    try:
        restore_backup(object_key=key)
        print("Restore successful.")
    except Exception:
        logger.exception("Restore failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
