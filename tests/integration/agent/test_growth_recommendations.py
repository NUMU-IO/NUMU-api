"""US2 — proactive growth recommendations (SC-003).

Verifies (a) every authored playbook is well-formed and resolvable (T026), and
(b) recommendations are made only for signals the store actually exhibits, each
tied to a real playbook with rationale + steps + citation; control stores get none.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.application.agent.knowledge.corpus_loader import load_authored_corpus
from src.application.agent.knowledge.growth import SIGNAL_DETECTORS, recommend_growth
from src.core.agent.knowledge import SourceKind


def _playbooks():
    return [d for d in load_authored_corpus() if d.source_kind == SourceKind.PLAYBOOK]


def test_playbooks_are_wellformed_and_resolvable():
    docs = load_authored_corpus()
    sources = {d.source for d in docs}
    playbooks = _playbooks()
    assert playbooks, "expected at least one growth playbook"
    for pb in playbooks:
        assert pb.area == "growth"
        assert pb.signal and pb.feature and pb.detected_by and pb.howto
        # detected_by must name a real, known live-signal detector
        assert pb.detected_by in SIGNAL_DETECTORS, pb.detected_by
        # howto must resolve to an existing how-to article
        assert pb.howto in sources, f"unresolved howto: {pb.howto}"


@pytest.mark.asyncio
async def test_recommendation_matches_the_store_signal(test_session):
    store_id = uuid4()
    # Store with abandoned carts only.
    metrics = {
        "orders.abandoned_count": 7,
        "catalog.bundle_like_count": 0,
        "orders.repeat_buyer_count": 0,
    }
    recs = await recommend_growth(test_session, store_id, metrics=metrics)
    features = {r["feature"] for r in recs}
    assert "abandoned-cart-recovery" in features
    # not recommended: no bundle / repeat-buyer signal
    assert "bogo" not in features
    assert "whatsapp-broadcasts" not in features
    # each rec is grounded + actionable
    for r in recs:
        assert r["source"] and r["rationale"] and r["howto"]


@pytest.mark.asyncio
async def test_control_store_with_no_signal_gets_nothing(test_session):
    metrics = {
        "orders.abandoned_count": 0,
        "catalog.bundle_like_count": 0,
        "orders.repeat_buyer_count": 0,
    }
    recs = await recommend_growth(test_session, uuid4(), metrics=metrics)
    assert recs == []  # relevant, not generic
