"""US2 write guards: permission, unavailable type, stale-proposal guard.

Spec US2 scenarios 5 & 6; FR-015 (stale-proposal guard); Constitution I/III.
"""

from __future__ import annotations

import copy
from uuid import uuid4

import pytest

from src.application.agent.proposals import StaleProposalError, apply_proposal
from src.application.agent.tools import ToolContext
from src.application.services.theme_v3_service import StaleEtagError
from src.core.agent.entities import (
    ActionProposal,
    Conversation,
    ProposalStatus,
)
from src.infrastructure.agent.persistence.repositories import (
    AuditRepository,
    ConversationRepository,
    ProposalRepository,
)
from src.infrastructure.agent.tools.theme_write import add_theme_section
from src.infrastructure.database.connection import set_tenant_id

_INITIAL = {"templates": {"home": {"name": "Home", "sections": {}, "order": []}}}
_SCHEMAS = {
    "testimonials": {"type": "testimonials", "name": "Testimonials", "settings": []}
}


class FakeV3Service:
    def __init__(self):
        self.published = copy.deepcopy(_INITIAL)
        self.draft = copy.deepcopy(_INITIAL)
        self.etag = "etag-1"

    async def get_draft_with_etag(self, store_id):
        return {"draft": copy.deepcopy(self.draft), "etag": self.etag}

    async def autosave_draft(
        self,
        *,
        store_id,
        payload,
        user_id=None,
        change_summary=None,
        expected_etag=None,
    ):
        if expected_etag is not None and expected_etag != self.etag:
            raise StaleEtagError(self.etag, self.draft)
        self.draft = copy.deepcopy(payload)
        return self.draft

    async def publish(self, *, store_id, user_id=None):
        self.published = copy.deepcopy(self.draft)
        return {"published": self.published, "revision_id": "rev-1"}


class FakeStoreTheme:
    section_schemas = _SCHEMAS
    theme_slug = "bazar"
    theme_name = "Bazar"
    customization_v3 = copy.deepcopy(_INITIAL)


def _patch(monkeypatch, fake):
    for mod in (
        "src.application.agent.proposals",
        "src.infrastructure.agent.tools.theme_write",
    ):
        monkeypatch.setattr(f"{mod}.build_v3_service", lambda session, _f=fake: _f)

    async def _get_active(self, store_id):
        return FakeStoreTheme()

    monkeypatch.setattr(
        "src.infrastructure.repositories.store_theme_repository.StoreThemeRepository.get_active_for_store",
        _get_active,
    )


def _ctx(session, perm):
    return ToolContext(
        tenant_id=uuid4(),
        store_id=uuid4(),
        staff_id=uuid4(),
        session=session,
        locale="en",
        has_permission=perm,
    )


@pytest.mark.asyncio
async def test_permission_denied_refused_before_preview(test_session):
    async def deny(_code):
        return False

    result = await add_theme_section(
        _ctx(test_session, deny), {"page": "home", "section_type": "testimonials"}
    )
    assert result.ok is False
    assert result.error_code == "forbidden"
    assert result.proposal is None  # no preview generated (scenario 6)


@pytest.mark.asyncio
async def test_unavailable_section_type_offers_alternatives(test_session, monkeypatch):
    _patch(monkeypatch, FakeV3Service())

    async def allow(_code):
        return True

    result = await add_theme_section(
        _ctx(test_session, allow), {"page": "home", "section_type": "carousel"}
    )
    assert result.ok is False
    assert result.error_code == "section_type_unavailable"
    assert "testimonials" in result.data["available_section_types"]
    assert result.proposal is None


@pytest.mark.asyncio
async def test_stale_proposal_guard_rejects_and_audits(test_session, monkeypatch):
    fake = FakeV3Service()
    _patch(monkeypatch, fake)
    tenant_id, store_id, staff_id = uuid4(), uuid4(), uuid4()

    async def allow(_code):
        return True

    try:
        set_tenant_id(tenant_id)
        ctx = ToolContext(
            tenant_id=tenant_id,
            store_id=store_id,
            staff_id=staff_id,
            session=test_session,
            locale="en",
            has_permission=allow,
        )
        result = await add_theme_section(
            ctx, {"page": "home", "section_type": "testimonials"}
        )
        conv = await ConversationRepository(test_session).create(
            Conversation(id=uuid4(), tenant_id=tenant_id, staff_id=staff_id)
        )
        proposal = await ProposalRepository(test_session).add(
            ActionProposal(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conv.id,
                tool_name="add_theme_section",
                params=result.proposal["params"],
                diff=result.proposal["diff"],
                store_id=store_id,
                based_on_theme_version=result.proposal["based_on_theme_version"],
            )
        )

        # The theme changed underneath the proposal.
        fake.etag = "etag-CHANGED"

        with pytest.raises(StaleProposalError):
            await apply_proposal(
                test_session,
                store_id=store_id,
                staff_id=staff_id,
                tenant_id=tenant_id,
                conversation_id=None,
                proposal_id=proposal.id,
            )

        # Proposal expired; a rejected audit was written; nothing published.
        assert (
            await ProposalRepository(test_session).get(proposal.id)
        ).status == ProposalStatus.EXPIRED
        last_applied = await AuditRepository(
            test_session
        ).get_last_applied_for_conversation(conv.id)
        assert last_applied is None  # no APPLIED record
        assert "testimonials-0" not in fake.published["templates"]["home"]["order"]
    finally:
        set_tenant_id(None)
