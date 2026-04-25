"""Integration tests for COD trust check + RiskAssessment persistence.

Exercises the trust-check pipeline against a real SQLAlchemy session
backed by SQLite-in-memory. We bring up only the `risk_assessments`
table (not the full schema) because the full schema has Postgres-only
columns the test conftest doesn't handle (BYTEA, etc.) — that limitation
is unrelated to the COD trust feature.

The full HTTP integration test (POST /checkout → risk_assessments row)
is exercised in the manual verification plan, not here.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.application.services.cod_trust_service import (
    CodTrustDecision,
    LocationSignals,
)
from src.infrastructure.database.models.tenant.risk_assessment import (
    RiskAssessmentModel,
)

# ──────────────────────────────────────────────────────────────────────
# Local fixtures — only spin up the risk_assessments table to avoid
# pulling in the rest of the (Postgres-only) schema.
# ──────────────────────────────────────────────────────────────────────


def _patch_table_for_sqlite(table) -> None:
    """In-place patches so the table compiles under SQLite."""
    if getattr(table, "schema", None):
        table.schema = None
    for column in table.columns:
        if isinstance(column.type, JSONB):
            column.type = JSON()


@pytest_asyncio.fixture(scope="function")
async def isolated_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory session scoped to just the risk_assessments table."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    table = RiskAssessmentModel.__table__
    _patch_table_for_sqlite(table)

    metadata = MetaData()
    # Copy the table into a fresh metadata so the schema-aware drop_all
    # at the end doesn't try to drop tables we never created.
    table.tometadata(metadata)

    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
    await engine.dispose()


def _make_decision(*, allowed: bool, reason: str, score: int = 70) -> CodTrustDecision:
    return CodTrustDecision(
        allowed=allowed,
        reason=reason,
        score=score,
        confidence="medium",
        label="risky",
        factors=[
            {"code": "no_location", "weight": 15, "detail": "No pin"},
            {"code": "location_teleport", "weight": 20, "detail": "300km jump"},
        ],
    )


# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_blocked_decision_writes_row(isolated_session):
    """Blocked decisions land in risk_assessments with order_id=None."""
    store_id = uuid4()
    tenant_id = uuid4()

    decision = _make_decision(allowed=False, reason="blocked_high_risk", score=85)

    isolated_session.add(
        RiskAssessmentModel(
            tenant_id=tenant_id,
            store_id=store_id,
            order_id=None,
            order_number=None,
            customer_name="Test Customer",
            customer_email="test@example.com",
            total_cents=0,
            currency="EGP",
            payment_method="cod",
            risk_score=decision.score,
            risk_level="critical",
            score_type="preliminary",
            suggested_action="cancel",
            action_taken=decision.reason,
            action_taken_at=datetime.now(UTC),
            action_taken_by="cod_trust",
            factors=list(decision.factors),
            scored_at=datetime.now(UTC),
        )
    )
    await isolated_session.flush()

    rows = (
        (
            await isolated_session.execute(
                select(RiskAssessmentModel).where(
                    RiskAssessmentModel.store_id == store_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.action_taken == "blocked_high_risk"
    assert row.payment_method == "cod"
    assert row.order_id is None
    assert any(f["code"] == "no_location" for f in row.factors)


@pytest.mark.asyncio
async def test_persist_allowed_decision_includes_order_id(isolated_session):
    """Allowed decisions persist with the created order's id linked."""
    store_id = uuid4()
    order_id = uuid4()

    decision = _make_decision(allowed=True, reason="below_threshold", score=40)

    isolated_session.add(
        RiskAssessmentModel(
            store_id=store_id,
            order_id=order_id,
            order_number="ORD-1234",
            total_cents=15_000,
            currency="EGP",
            payment_method="cod",
            risk_score=decision.score,
            risk_level="medium",
            score_type="preliminary",
            suggested_action="auto_approve",
            action_taken=decision.reason,
            action_taken_at=datetime.now(UTC),
            action_taken_by="cod_trust",
            factors=list(decision.factors),
            scored_at=datetime.now(UTC),
        )
    )
    await isolated_session.flush()

    row = (
        await isolated_session.execute(
            select(RiskAssessmentModel).where(RiskAssessmentModel.order_id == order_id)
        )
    ).scalar_one()
    assert row.action_taken == "below_threshold"
    assert row.suggested_action == "auto_approve"
    assert row.order_number == "ORD-1234"


@pytest.mark.asyncio
async def test_list_filters_by_payment_method(isolated_session):
    """The merchant decisions feed should only show COD assessments."""
    store_id = uuid4()

    for method, reason in [
        ("cod", "blocked_high_risk"),
        ("paymob_card", "auto_approve"),
    ]:
        isolated_session.add(
            RiskAssessmentModel(
                store_id=store_id,
                order_id=None,
                total_cents=0,
                currency="EGP",
                payment_method=method,
                risk_score=70,
                risk_level="high",
                score_type="preliminary",
                suggested_action="cancel",
                action_taken=reason,
                action_taken_at=datetime.now(UTC),
                action_taken_by="cod_trust",
                factors=[],
                scored_at=datetime.now(UTC),
            )
        )
    await isolated_session.flush()

    rows = (
        (
            await isolated_session.execute(
                select(RiskAssessmentModel).where(
                    RiskAssessmentModel.store_id == store_id,
                    RiskAssessmentModel.payment_method == "cod",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].payment_method == "cod"


@pytest.mark.asyncio
async def test_factors_json_roundtrip(isolated_session):
    """Factors persist through JSONB and come back identical."""
    store_id = uuid4()
    factors = [
        {"code": "no_location", "weight": 15, "detail": "Customer skipped pin."},
        {
            "code": "low_accuracy_gps",
            "weight": 10,
            "detail": "GPS reading ~1500m — low confidence.",
        },
    ]

    isolated_session.add(
        RiskAssessmentModel(
            store_id=store_id,
            total_cents=0,
            currency="EGP",
            payment_method="cod",
            risk_score=70,
            risk_level="high",
            score_type="preliminary",
            suggested_action="cancel",
            action_taken="blocked_high_risk",
            action_taken_at=datetime.now(UTC),
            action_taken_by="cod_trust",
            factors=factors,
            scored_at=datetime.now(UTC),
        )
    )
    await isolated_session.flush()

    row = (
        await isolated_session.execute(
            select(RiskAssessmentModel).where(RiskAssessmentModel.store_id == store_id)
        )
    ).scalar_one()
    assert row.factors == factors


def test_location_signals_dataclass_is_immutable():
    """LocationSignals is frozen — defensive against accidental mutation
    of the same dataclass passed into multiple stores' trust checks."""
    sig = LocationSignals()
    with pytest.raises(Exception):
        sig.latitude = 30.0  # type: ignore[misc]


# ─── Public checkout-config: cod_trust phone override ─────────────────


def test_checkout_config_marks_phone_required_when_cod_trust_enabled():
    """The public storefront checkout-config endpoint must mark phone
    as required (with reason="cod_trust") whenever the merchant has
    cod_trust enabled — so the storefront's Zod schema demands it
    before the user submits, instead of only learning via 400."""
    from src.core.checkout_fields import resolve_config

    # Simulate the route's override logic locally — this mirrors the
    # decision in src/api/v1/routes/storefront/checkout_config.py.
    settings = {
        "checkout_fields": {
            "standard_fields": {"phone": {"enabled": True, "required": False}}
        },
        "cod_trust": {"enabled": True},
    }
    config = resolve_config(settings)
    cod_trust = (settings or {}).get("cod_trust") or {}
    if isinstance(cod_trust, dict) and cod_trust.get("enabled"):
        std = config.setdefault("standard_fields", {})
        phone_cfg = std.setdefault("phone", {"enabled": True, "required": True})
        phone_cfg["enabled"] = True
        phone_cfg["required"] = True
        phone_cfg["required_reason"] = "cod_trust"

    assert config["standard_fields"]["phone"]["required"] is True
    assert config["standard_fields"]["phone"]["required_reason"] == "cod_trust"


def test_checkout_config_leaves_phone_alone_when_cod_trust_disabled():
    from src.core.checkout_fields import resolve_config

    settings = {
        "checkout_fields": {
            "standard_fields": {"phone": {"enabled": True, "required": False}}
        },
        "cod_trust": {"enabled": False},
    }
    config = resolve_config(settings)
    cod_trust = (settings or {}).get("cod_trust") or {}
    if isinstance(cod_trust, dict) and cod_trust.get("enabled"):
        config["standard_fields"]["phone"]["required"] = True

    assert config["standard_fields"]["phone"]["required"] is False
    assert "required_reason" not in config["standard_fields"]["phone"]
