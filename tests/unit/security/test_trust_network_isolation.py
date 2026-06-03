"""P1-6: trust-network tenant-isolation invariants (structural regression).

Guards the model documented in docs/security/trust-network-isolation.md so a
future schema change can't silently break the isolation guarantees a
due-diligence reviewer relies on.
"""

from __future__ import annotations

from src.infrastructure.database.models.tenant.network_contribution_log import (
    NetworkContributionLogModel,
)
from src.infrastructure.database.models.tenant.network_reputation import (
    NetworkReputationModel,
)
from src.infrastructure.database.models.tenant.risk_assessment import (
    RiskAssessmentModel,
)


def _columns(model) -> set[str]:
    return set(model.__table__.columns.keys())


def test_network_reputation_is_global_no_tenant_or_store():
    """The moat is intentionally cross-merchant: no tenant/store partition."""
    cols = _columns(NetworkReputationModel)
    assert "tenant_id" not in cols
    assert "store_id" not in cols
    assert "phone_hash" in cols
    assert NetworkReputationModel.__table__.c.phone_hash.unique is True


def test_network_reputation_holds_no_raw_pii():
    cols = _columns(NetworkReputationModel)
    forbidden = {
        "phone",
        "phone_number",
        "customer_name",
        "name",
        "email",
        "address",
    }
    leaked = forbidden & cols
    assert not leaked, f"raw PII column leaked into the global moat: {leaked}"


def test_risk_assessment_is_store_scoped():
    """public schema, but every query scopes by store_id (Store A != Store B)."""
    assert "store_id" in _columns(RiskAssessmentModel)


def test_contribution_log_is_store_scoped():
    """store_id drives the GDPR decrement + contributing-store count."""
    assert "store_id" in _columns(NetworkContributionLogModel)
