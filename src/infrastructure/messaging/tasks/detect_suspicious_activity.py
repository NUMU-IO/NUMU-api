"""Detect suspicious staff activity."""

import logging
from datetime import datetime

from sqlalchemy import select

from src.infrastructure.database.connection import get_db_session
from src.infrastructure.database.models.public.permission_change_log import (
    PermissionChangeLogModel,
    PermissionChangeTargetType,
)
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.services.staff_risk_service import SuspiciousActivityDetector

logger = logging.getLogger(__name__)


@celery_app.task(name="detect_suspicious_activity")
async def detect_suspicious_activity() -> dict:
    """Detect suspicious staff activity patterns."""
    async with get_db_session() as db:
        detector = SuspiciousActivityDetector(db)

        result = await db.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.status == "active",
            )
        )
        memberships = list(result.scalars().all())

        alerts = []
        for membership in memberships:
            tenant_id = membership.tenant_id
            user_id = membership.user_id

            if await detector.check_mass_export(tenant_id, user_id):
                alerts.append({
                    "membership_id": str(membership.id),
                    "signal": "mass_export",
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),
                })

            if await detector.check_off_hours_activity(membership.id):
                alerts.append({
                    "membership_id": str(membership.id),
                    "signal": "off_hours_activity",
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),
                })

            if await detector.check_permission_probing(tenant_id, user_id):
                alerts.append({
                    "membership_id": str(membership.id),
                    "signal": "permission_probing",
                    "tenant_id": str(tenant_id),
                    "user_id": str(user_id),
                })

        for alert in alerts:
            log = PermissionChangeLogModel(
                tenant_id=alert.get("tenant_id"),
                actor_user_id=alert.get("user_id"),
                target_type=PermissionChangeTargetType.MEMBERSHIP,
                target_id=alert["membership_id"],
                action="suspicious_activity",
                after={"signal": alert["signal"]},
                created_at=datetime.utcnow(),
            )
            db.add(log)

        await db.commit()

        return {
            "alerts": len(alerts),
            "checked_at": datetime.utcnow().isoformat(),
        }


@celery_app.task(name="compute_staff_risk_scores")
async def compute_staff_risk_scores(tenant_id: str | None = None) -> dict:
    """Compute risk scores for all staff memberships."""
    async with get_db_session() as db:
        from uuid import UUID

        calc = RiskScoreCalculator(db)

        if tenant_id:
            scores = await calc.get_risk_scores_for_tenant(UUID(tenant_id))
        else:
            result = await db.execute(
                select(TenantMembershipModel).where(
                    TenantMembershipModel.status == "active",
                )
            )
            memberships = list(result.scalars().all())
            scores = {}
            for m in memberships:
                score, details = await calc.calculate_risk_score(m.id)
                scores[str(m.user_id)] = (score, details)

        high_risk = [uid for uid, (score, _) in scores.items() if score >= 7]

        return {
            "scores": {
                uid: {"score": score, "details": details}
                for uid, (score, details) in scores.items()
            },
            "high_risk_count": len(high_risk),
            "high_risk_members": high_risk,
            "computed_at": datetime.utcnow().isoformat(),
        }


from src.infrastructure.services.staff_risk_service import RiskScoreCalculator
