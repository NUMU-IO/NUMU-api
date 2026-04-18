"""Staff risk scoring service."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.permission_change_log import (
    PermissionChangeAction,
    PermissionChangeLogModel,
)
from src.infrastructure.database.models.public.staff_access_policy import (
    StaffAccessPolicyModel,
)
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)


class RiskScoreCalculator:
    """Calculates risk scores for staff memberships."""

    WEIGHTS = {
        "high_risk_perm": 2,
        "critical_risk_perm": 5,
        "no_2fa": 10,
        "no_ip_allowlist": 3,
        "recent_sensitive_action": 2,
    }

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def calculate_risk_score(
        self,
        membership_id: UUID,
    ) -> tuple[int, dict]:
        """Calculate risk score for a membership.

        Returns:
            Tuple of (score, details)
        """
        score = 0
        details: dict = {
            "high_risk_perms": 0,
            "critical_risk_perms": 0,
            "no_2fa": False,
            "no_ip_allowlist": False,
            "recent_sensitive_actions": 0,
        }

        result = await self.session.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.id == membership_id
            )
        )
        membership = result.scalar_one_or_none()
        if not membership:
            return 0, details

        if membership.two_factor_required:
            score += self.WEIGHTS["no_2fa"]
            details["no_2fa"] = True

        policy_result = await self.session.execute(
            select(StaffAccessPolicyModel).where(
                StaffAccessPolicyModel.membership_id == membership_id
            )
        )
        policy = policy_result.scalar_one_or_none()
        if policy and not policy.ip_allowlist:
            score += self.WEIGHTS["no_ip_allowlist"]
            details["no_ip_allowlist"] = True

        since = datetime.utcnow() - timedelta(hours=24)
        sensitive_result = await self.session.execute(
            select(PermissionChangeLogModel).where(
                PermissionChangeLogModel.tenant_id == membership.tenant_id,
                PermissionChangeLogModel.actor_user_id == membership.user_id,
                PermissionChangeLogModel.created_at > since,
                PermissionChangeLogModel.action.in_([
                    PermissionChangeAction.PERM_ADDED,
                    PermissionChangeAction.ROLE_ASSIGNED,
                ]),
            )
        )
        sensitive_logs = list(sensitive_result.scalars().all())
        details["recent_sensitive_actions"] = len(sensitive_logs)
        score += len(sensitive_logs) * self.WEIGHTS["recent_sensitive_action"]

        return score, details

    async def get_risk_scores_for_tenant(
        self,
        tenant_id: UUID,
    ) -> dict[str, tuple[int, dict]]:
        """Get risk scores for all memberships in a tenant."""
        result = await self.session.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.status != "revoked",
            )
        )
        memberships = list(result.scalars().all())

        scores: dict[str, tuple[int, dict]] = {}
        for membership in memberships:
            score, details = await self.calculate_risk_score(membership.id)
            scores[str(membership.user_id)] = (score, details)

        return scores


class SuspiciousActivityDetector:
    """Detects suspicious staff activity patterns."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check_mass_export(
        self,
        tenant_id: UUID,
        user_id: UUID,
        window_minutes: int = 60,
    ) -> bool:
        """Check for mass export activity."""
        since = datetime.utcnow() - timedelta(minutes=window_minutes)
        result = await self.session.execute(
            select(PermissionChangeLogModel).where(
                PermissionChangeLogModel.tenant_id == tenant_id,
                PermissionChangeLogModel.actor_user_id == user_id,
                PermissionChangeLogModel.created_at > since,
                PermissionChangeLogModel.action == PermissionChangeAction.UPDATED,
            )
        )
        logs = list(result.scalars().all())
        export_count = sum(
            1
            for log in logs
            if log.after and "export" in log.after.get("permission_code", "")
        )
        return export_count > 10

    async def check_off_hours_activity(
        self,
        membership_id: UUID,
    ) -> bool:
        """Check for activity outside working hours."""
        result = await self.session.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.id == membership_id
            )
        )
        membership = result.scalar_one_or_none()
        if not membership:
            return False

        policy_result = await self.session.execute(
            select(StaffAccessPolicyModel).where(
                StaffAccessPolicyModel.membership_id == membership_id
            )
        )
        policy = policy_result.scalar_one_or_none()
        if not policy or not policy.working_hours:
            return False

        now = datetime.utcnow()
        working_hours = policy.working_hours
        tz = working_hours.get("tz", "Africa/Cairo")
        windows = working_hours.get("windows", [])

        if not windows:
            return False

        import zoneinfo

        try:
            tz_obj = zoneinfo.ZoneInfo(tz)
            local_now = now.astimezone(tz_obj)
            current_dow = local_now.isoweekday()
            current_time = local_now.time()

            for window in windows:
                if current_dow not in window.get("dow", []):
                    continue
                start = window.get("start", "00:00")
                end = window.get("end", "23:59")
                from datetime import time as dt_time

                start_time = dt_time.fromisoformat(start)
                end_time = dt_time.fromisoformat(end)
                if start_time <= current_time <= end_time:
                    return False

            return True
        except Exception:
            return False

    async def check_permission_probing(
        self,
        tenant_id: UUID,
        user_id: UUID,
        window_minutes: int = 5,
    ) -> bool:
        """Check for rapid permission denial probing."""
        since = datetime.utcnow() - timedelta(minutes=window_minutes)
        result = await self.session.execute(
            select(PermissionChangeLogModel).where(
                PermissionChangeLogModel.tenant_id == tenant_id,
                PermissionChangeLogModel.actor_user_id == user_id,
                PermissionChangeLogModel.created_at > since,
                PermissionChangeLogModel.action == PermissionChangeAction.OVERRIDE_SET,
            )
        )
        logs = list(result.scalars().all())
        denial_count = sum(
            1 for log in logs if log.after and log.after.get("effect") == "deny"
        )
        return denial_count > 20
