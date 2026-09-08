"""Confirm-time guards: the right permission, the right store, the draft only.

Three regressions found in the 2026-09-08 audit, all on the apply path:

* every proposal was gated on a hardcoded ``themes.edit``;
* a proposal was not bound to the store it was built against;
* confirming a theme change published it straight to the storefront.
"""

from __future__ import annotations

import copy
from uuid import uuid4

import pytest

from src.application.agent.proposals import (
    PermissionDeniedError,
    ProposalError,
    apply_proposal,
    permission_for_tool,
)
from src.core.agent.entities import ActionProposal, Conversation, ProposalStatus
from src.infrastructure.agent.persistence.repositories import (
    ConversationRepository,
    ProposalRepository,
)
from src.infrastructure.database.connection import set_tenant_id

_INITIAL = {"templates": {"home": {"name": "Home", "sections": {}, "order": []}}}


class FakeV3Service:
    """Records whether publish was reached — the point of the third test."""

    def __init__(self):
        self.published = copy.deepcopy(_INITIAL)
        self.draft = copy.deepcopy(_INITIAL)
        self.etag = "etag-1"
        self.publish_calls = 0

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
        self.draft = copy.deepcopy(payload)
        return self.draft

    async def publish(self, *, store_id, user_id=None):
        self.publish_calls += 1
        self.published = copy.deepcopy(self.draft)
        return {"published": self.published, "revision_id": "rev-1"}


class FakeStoreTheme:
    section_schemas = {
        "testimonials": {"type": "testimonials", "name": "Testimonials", "settings": []}
    }
    theme_slug = "bazar"
    theme_name = "Bazar"
    customization_v3 = copy.deepcopy(_INITIAL)


def _patch(monkeypatch, fake):
    monkeypatch.setattr(
        "src.application.agent.proposals.build_v3_service", lambda session: fake
    )

    async def _get_active(self, store_id):
        return FakeStoreTheme()

    monkeypatch.setattr(
        "src.infrastructure.repositories.store_theme_repository."
        "StoreThemeRepository.get_active_for_store",
        _get_active,
    )


async def _make_proposal(session, *, tenant_id, staff_id, store_id, tool_name, params):
    conv = await ConversationRepository(session).create(
        Conversation(id=uuid4(), tenant_id=tenant_id, staff_id=staff_id)
    )
    return await ProposalRepository(session).add(
        ActionProposal(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conv.id,
            tool_name=tool_name,
            params=params,
            diff={},
            store_id=store_id,
            based_on_theme_version="etag-1",
        )
    )


def _only(*granted):
    async def check(code):
        return code in granted

    return check


class TestPermissionIsPerTool:
    def test_each_tool_asks_for_its_own_permission(self):
        assert permission_for_tool("create_discount") != "themes.edit"
        assert permission_for_tool("add_theme_section") == "themes.edit"

    def test_unknown_tool_fails_closed(self):
        """Never fall through to "no permission required"."""
        assert permission_for_tool("not_a_real_tool") == "themes.edit"

    @pytest.mark.asyncio
    async def test_theme_permission_cannot_confirm_a_discount(self, test_session):
        """The escalation: themes.edit used to authorize every write."""
        tenant_id, store_id, staff_id = uuid4(), uuid4(), uuid4()
        try:
            set_tenant_id(tenant_id)
            proposal = await _make_proposal(
                test_session,
                tenant_id=tenant_id,
                staff_id=staff_id,
                store_id=store_id,
                tool_name="create_discount",
                params={"code": "X", "discount_type": "percentage", "value": 10},
            )
            with pytest.raises(PermissionDeniedError):
                await apply_proposal(
                    test_session,
                    store_id=store_id,
                    staff_id=staff_id,
                    tenant_id=tenant_id,
                    conversation_id=None,
                    proposal_id=proposal.id,
                    has_permission=_only("themes.edit"),
                )
            assert (
                await ProposalRepository(test_session).get(proposal.id)
            ).status == ProposalStatus.PENDING
        finally:
            set_tenant_id(None)


class TestProposalIsBoundToItsStore:
    @pytest.mark.asyncio
    async def test_confirming_from_another_store_is_refused(
        self, test_session, monkeypatch
    ):
        fake = FakeV3Service()
        _patch(monkeypatch, fake)
        tenant_id, store_a, store_b, staff_id = uuid4(), uuid4(), uuid4(), uuid4()
        try:
            set_tenant_id(tenant_id)
            proposal = await _make_proposal(
                test_session,
                tenant_id=tenant_id,
                staff_id=staff_id,
                store_id=store_a,
                tool_name="add_theme_section",
                params={
                    "page": "home",
                    "new_section_id": "testimonials-0",
                    "section_type": "testimonials",
                },
            )
            with pytest.raises(ProposalError) as err:
                await apply_proposal(
                    test_session,
                    store_id=store_b,  # same tenant, different store
                    staff_id=staff_id,
                    tenant_id=tenant_id,
                    conversation_id=None,
                    proposal_id=proposal.id,
                    has_permission=_only("themes.edit"),
                )
            assert err.value.code == "wrong_store"
            assert fake.draft == _INITIAL  # nothing written to either store
        finally:
            set_tenant_id(None)

    @pytest.mark.asyncio
    async def test_a_proposal_without_a_store_is_refused(
        self, test_session, monkeypatch
    ):
        """Rows predating the column are refused, never trusted."""
        _patch(monkeypatch, FakeV3Service())
        tenant_id, store_id, staff_id = uuid4(), uuid4(), uuid4()
        try:
            set_tenant_id(tenant_id)
            proposal = await _make_proposal(
                test_session,
                tenant_id=tenant_id,
                staff_id=staff_id,
                store_id=None,
                tool_name="add_theme_section",
                params={
                    "page": "home",
                    "new_section_id": "testimonials-0",
                    "section_type": "testimonials",
                },
            )
            with pytest.raises(ProposalError) as err:
                await apply_proposal(
                    test_session,
                    store_id=store_id,
                    staff_id=staff_id,
                    tenant_id=tenant_id,
                    conversation_id=None,
                    proposal_id=proposal.id,
                    has_permission=_only("themes.edit"),
                )
            assert err.value.code == "wrong_store"
        finally:
            set_tenant_id(None)


class TestConfirmDoesNotPublish:
    @pytest.mark.asyncio
    async def test_change_lands_in_the_draft_only(self, test_session, monkeypatch):
        fake = FakeV3Service()
        _patch(monkeypatch, fake)
        tenant_id, store_id, staff_id = uuid4(), uuid4(), uuid4()
        try:
            set_tenant_id(tenant_id)
            proposal = await _make_proposal(
                test_session,
                tenant_id=tenant_id,
                staff_id=staff_id,
                store_id=store_id,
                tool_name="add_theme_section",
                params={
                    "page": "home",
                    "new_section_id": "testimonials-0",
                    "section_type": "testimonials",
                },
            )
            out = await apply_proposal(
                test_session,
                store_id=store_id,
                staff_id=staff_id,
                tenant_id=tenant_id,
                conversation_id=None,
                proposal_id=proposal.id,
                has_permission=_only("themes.edit"),
            )

            assert out["applied"] is True
            assert out["published"] is False
            assert fake.publish_calls == 0
            # In the draft the customizer reads...
            assert "testimonials-0" in fake.draft["templates"]["home"]["order"]
            # ...and not on the storefront until the merchant presses Update.
            assert "testimonials-0" not in fake.published["templates"]["home"]["order"]
        finally:
            set_tenant_id(None)
