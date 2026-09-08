"""US2 add-a-section happy path: propose -> confirm -> apply -> audit -> undo.

Spec US2 scenarios 1, 2, 4; SC-002/SC-003. The theme-editor-v3 write surface is
faked (seeding the full theme stack on SQLite is impractical); the test exercises
the Agent's own proposal/apply/undo + audit logic against that surface.
"""

from __future__ import annotations

import copy
from uuid import uuid4

import pytest

from src.application.agent.proposals import apply_proposal, undo_last
from src.application.agent.tools import ToolContext
from src.application.services.theme_v3_service import StaleEtagError
from src.core.agent.entities import (
    ActionProposal,
    AuditResult,
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
    "testimonials": {
        "type": "testimonials",
        "name": "Testimonials",
        "settings": [
            {"id": "title", "type": "text", "default": "What our customers say"}
        ],
    }
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
        self.etag = f"{self.etag}+"
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
        "src.infrastructure.agent.tools.theme_read",
    ):
        monkeypatch.setattr(f"{mod}.build_v3_service", lambda session, _f=fake: _f)

    async def _get_active(self, store_id):
        return FakeStoreTheme()

    monkeypatch.setattr(
        "src.infrastructure.repositories.store_theme_repository.StoreThemeRepository.get_active_for_store",
        _get_active,
    )


async def _allow(_code: str) -> bool:
    return True


@pytest.mark.asyncio
async def test_propose_confirm_apply_audit_undo(test_session, monkeypatch):
    fake = FakeV3Service()
    _patch(monkeypatch, fake)
    tenant_id, store_id, staff_id = uuid4(), uuid4(), uuid4()

    try:
        set_tenant_id(tenant_id)
        ctx = ToolContext(
            tenant_id=tenant_id,
            store_id=store_id,
            staff_id=staff_id,
            session=test_session,
            locale="en",
            has_permission=_allow,
        )

        # 1. Propose — nothing applied yet.
        result = await add_theme_section(
            ctx, {"page": "home", "section_type": "testimonials"}
        )
        assert result.ok and result.proposal is not None
        assert result.proposal["params"]["new_section_id"] == "testimonials-0"
        assert fake.published["templates"]["home"]["order"] == []  # not applied

        # 2. Persist conversation + proposal (as run_turn would).
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

        # 3. Confirm/apply.
        applied = await apply_proposal(
            test_session,
            store_id=store_id,
            staff_id=staff_id,
            tenant_id=tenant_id,
            conversation_id=None,
            proposal_id=proposal.id,
            model_used="fake",
        )
        assert applied["applied"] is True
        # Confirm writes the draft the customizer reads; the storefront only
        # changes when the merchant presses Update.
        assert applied["published"] is False
        assert "testimonials-0" in fake.draft["templates"]["home"]["order"]
        assert "testimonials-0" not in fake.published["templates"]["home"]["order"]

        # Exactly one applied audit; proposal now applied (SC-002/SC-003).
        audit = await AuditRepository(test_session).get_last_applied_for_conversation(
            conv.id
        )
        assert audit is not None and audit.result == AuditResult.APPLIED
        assert (
            await ProposalRepository(test_session).get(proposal.id)
        ).status == ProposalStatus.APPLIED

        # 4. Undo restores the prior state.
        undone = await undo_last(
            test_session,
            store_id=store_id,
            staff_id=staff_id,
            tenant_id=tenant_id,
            conversation_id=conv.id,
            model_used="fake",
        )
        assert undone["undone"] is True
        assert "testimonials-0" not in fake.draft["templates"]["home"]["order"]
    finally:
        set_tenant_id(None)
