"""meta_tracking_prune deletes match-quality snapshots past their retention.

The prune aged every table by ``created_at``. The snapshot table only has
``captured_at``, so its batch raised AttributeError, was logged and skipped,
and no snapshot was ever deleted (worker log 2026-10-15..18).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import select

from src.infrastructure.database.models.tenant.meta_match_quality_snapshot import (
    MetaMatchQualitySnapshotModel,
)
from src.infrastructure.messaging.tasks.meta_capi import _prune_tracking_rows


class _SessionCM:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


async def test_snapshots_past_retention_are_deleted(test_session):
    def snapshot(age_days):
        return MetaMatchQualitySnapshotModel(
            tenant_id=uuid4(),
            store_id=uuid4(),
            pixel_id="1",
            event_name="Purchase",
            emq_score=6.1,
            captured_at=datetime.now(UTC) - timedelta(days=age_days),
        )

    old, fresh = snapshot(200), snapshot(1)
    test_session.add_all([old, fresh])
    await test_session.commit()

    with (
        patch(
            "src.infrastructure.database.connection.AsyncSessionLocal",
            lambda: _SessionCM(test_session),
        ),
        patch("src.infrastructure.tenancy.rls.enable_rls_bypass", new=AsyncMock()),
    ):
        stats = await _prune_tracking_rows()

    assert stats["match_quality_deleted"] == 1
    left = await test_session.scalars(select(MetaMatchQualitySnapshotModel.id))
    assert left.all() == [fresh.id]
