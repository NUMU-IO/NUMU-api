"""The database backup reaches R2 through either credential set.

It read only the legacy ``R2_*`` aliases, while ``.env.example`` configures
production R2 through ``S3_*`` alone; with only those set, every dump would
have been thrown away at upload.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

# By path: this test package is itself named ``scripts``.
_spec = importlib.util.spec_from_file_location(
    "backup_db", Path(__file__).parents[3] / "scripts" / "backup_db.py"
)
backup_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup_db)
_build_r2_client = backup_db._build_r2_client

NO_R2 = {"r2_account_id": None, "r2_access_key_id": None, "r2_secret_access_key": None}
NO_S3 = {
    "s3_endpoint_url": None,
    "s3_access_key_id": None,
    "s3_secret_access_key": None,
}


def test_s3_settings_alone_are_enough():
    settings = SimpleNamespace(
        **NO_R2,
        s3_endpoint_url="https://acct.r2.cloudflarestorage.com",
        s3_access_key_id="key",
        s3_secret_access_key="secret",
    )
    assert _build_r2_client(settings).meta.endpoint_url == (
        "https://acct.r2.cloudflarestorage.com"
    )


def test_r2_settings_still_win():
    settings = SimpleNamespace(
        r2_account_id="acct",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
        s3_endpoint_url="http://localhost:9000",
        s3_access_key_id="minio",
        s3_secret_access_key="minio",
    )
    assert _build_r2_client(settings).meta.endpoint_url == (
        "https://acct.r2.cloudflarestorage.com"
    )


def test_no_credentials_is_an_error():
    with pytest.raises(RuntimeError, match="not configured"):
        _build_r2_client(SimpleNamespace(**NO_R2, **NO_S3))
