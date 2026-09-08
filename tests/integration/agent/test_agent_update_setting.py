"""US3 edit-text: update_theme_setting propose -> confirm -> undo (reuses US2 path).

Spec US3 scenarios 1 & 3. The theme-editor-v3 surface is faked.
"""

from __future__ import annotations

import copy
from uuid import uuid4

import pytest

from src.application.agent.proposals import apply_proposal, undo_last
from src.application.agent.tools import ToolContext
from src.application.services.theme_v3_service import StaleEtagError
from src.core.agent.entities import ActionProposal, Conversation
from src.infrastructure.agent.persistence.repositories import (
    ConversationRepository,
    ProposalRepository,
)
from src.infrastructure.agent.tools.theme_write import update_theme_setting
from src.infrastructure.database.connection import set_tenant_id

_INITIAL = {
    "global_settings": {"hero_title": "Old heading"},
    "templates": {"home": {"name": "Home", "sections": {}, "order": []}},
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
    section_schemas = {}
    settings_schema = [{"id": "hero_title", "type": "text", "default": "Welcome"}]
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


@pytest.mark.asyncio
async def test_update_setting_propose_confirm_undo(test_session, monkeypatch):
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

        result = await update_theme_setting(
            ctx, {"setting_path": "global.hero_title", "value": "Summer Sale"}
        )
        assert result.ok and result.proposal is not None
        assert result.proposal["diff"] == {
            "setting_path": "global.hero_title",
            "before": "Old heading",
            "after": "Summer Sale",
        }
        assert (
            fake.published["global_settings"]["hero_title"] == "Old heading"
        )  # not applied

        conv = await ConversationRepository(test_session).create(
            Conversation(id=uuid4(), tenant_id=tenant_id, staff_id=staff_id)
        )
        proposal = await ProposalRepository(test_session).add(
            ActionProposal(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conv.id,
                tool_name="update_theme_setting",
                params=result.proposal["params"],
                diff=result.proposal["diff"],
                store_id=store_id,
                based_on_theme_version=result.proposal["based_on_theme_version"],
            )
        )

        await apply_proposal(
            test_session,
            store_id=store_id,
            staff_id=staff_id,
            tenant_id=tenant_id,
            conversation_id=None,
            proposal_id=proposal.id,
        )
        assert fake.draft["global_settings"]["hero_title"] == "Summer Sale"
        assert fake.published["global_settings"]["hero_title"] == "Old heading"

        await undo_last(
            test_session,
            store_id=store_id,
            staff_id=staff_id,
            tenant_id=tenant_id,
            conversation_id=conv.id,
        )
        assert fake.draft["global_settings"]["hero_title"] == "Old heading"
    finally:
        set_tenant_id(None)
