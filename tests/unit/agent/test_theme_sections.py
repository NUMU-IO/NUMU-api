"""Editing and removing sections: preview only, and never widen the blast radius."""

from __future__ import annotations

import copy
from uuid import uuid4

import pytest

from src.application.agent.proposals import _apply_change
from src.application.agent.tools import ToolContext
from src.core.agent.entities import RiskTier
from src.infrastructure.agent.tools import theme_sections
from src.infrastructure.agent.tools.theme_sections import (
    REMOVE_SECTION_SPEC,
    UPDATE_SECTION_SPEC,
    remove_section,
    update_section_settings,
)

DRAFT = {
    "templates": {
        "home": {
            "sections": {
                "hero-0": {
                    "type": "hero",
                    "settings": {
                        "heading": "Old",
                        "subheading": "Sub",
                        "align": "left",
                    },
                },
                "testimonials-0": {"type": "testimonials", "settings": {}},
            },
            "order": ["hero-0", "testimonials-0"],
        }
    }
}

SCHEMAS = {
    "hero": {
        "type": "hero",
        "settings": [
            {"id": "heading", "default": "Welcome"},
            {"id": "subheading", "default": ""},
            {"id": "align", "default": "center"},
        ],
    },
    "testimonials": {"type": "testimonials", "settings": []},
}


class _Theme:
    section_schemas = SCHEMAS


@pytest.fixture(autouse=True)
def _theme(monkeypatch):
    class _Service:
        async def get_draft_with_etag(self, _store_id):
            return {"draft": copy.deepcopy(DRAFT), "etag": "etag-1"}

    class _Repo:
        def __init__(self, _s):
            pass

        async def get_active_for_store(self, _store_id):
            return _Theme()

    monkeypatch.setattr(theme_sections, "build_v3_service", lambda s: _Service())
    monkeypatch.setattr(theme_sections, "StoreThemeRepository", _Repo)


def _ctx(*, allowed=True, locale="en"):
    async def perm(_c):
        return allowed

    return ToolContext(
        tenant_id=uuid4(),
        store_id=uuid4(),
        staff_id=uuid4(),
        session=object(),
        locale=locale,
        has_permission=perm,
    )


class TestUpdateSectionSettings:
    @pytest.mark.asyncio
    async def test_it_previews_a_real_before_and_after(self):
        res = await update_section_settings(
            _ctx(),
            {"page": "home", "section_id": "hero-0", "settings": {"heading": "New"}},
        )
        assert res.ok
        assert res.data["diff"]["before"] == {"heading": "Old"}
        assert res.data["diff"]["after"] == {"heading": "New"}
        assert res.proposal["based_on_theme_version"] == "etag-1"

    @pytest.mark.asyncio
    async def test_several_settings_change_in_one_proposal(self):
        res = await update_section_settings(
            _ctx(),
            {
                "page": "home",
                "section_id": "hero-0",
                "settings": {"heading": "New", "align": "center"},
            },
        )
        assert res.ok
        assert set(res.data["diff"]["after"]) == {"heading", "align"}

    @pytest.mark.asyncio
    async def test_unspecified_settings_are_left_alone_on_apply(self):
        """The regression this tool could easily have shipped: applying schema
        defaults would silently reset settings the merchant never mentioned."""
        draft = copy.deepcopy(DRAFT)
        _apply_change(
            "update_section_settings",
            draft,
            {"page": "home", "section_id": "hero-0", "settings": {"heading": "New"}},
        )
        settings = draft["templates"]["home"]["sections"]["hero-0"]["settings"]
        assert settings["heading"] == "New"
        assert settings["subheading"] == "Sub"  # untouched, not reset to ""
        assert settings["align"] == "left"

    @pytest.mark.asyncio
    async def test_a_setting_the_section_type_does_not_have_is_refused(self):
        res = await update_section_settings(
            _ctx(),
            {"page": "home", "section_id": "hero-0", "settings": {"nonsense": 1}},
        )
        assert res.ok is False
        assert "nonsense" in res.error_message
        assert res.proposal is None

    @pytest.mark.asyncio
    async def test_a_missing_page_or_section_says_what_exists(self):
        res = await update_section_settings(
            _ctx(),
            {"page": "about", "section_id": "hero-0", "settings": {"heading": "x"}},
        )
        assert res.ok is False and "home" in res.error_message

        res = await update_section_settings(
            _ctx(),
            {"page": "home", "section_id": "ghost-9", "settings": {"heading": "x"}},
        )
        assert res.ok is False and "hero-0" in res.error_message

    @pytest.mark.asyncio
    async def test_a_no_op_is_refused_rather_than_previewed(self):
        res = await update_section_settings(
            _ctx(),
            {"page": "home", "section_id": "hero-0", "settings": {"heading": "Old"}},
        )
        assert res.ok is False

    @pytest.mark.asyncio
    async def test_permission_is_required(self):
        res = await update_section_settings(
            _ctx(allowed=False),
            {"page": "home", "section_id": "hero-0", "settings": {"heading": "x"}},
        )
        assert res.ok is False and res.error_code == "forbidden"


class TestRemoveSection:
    @pytest.mark.asyncio
    async def test_it_previews_the_order_change_and_carries_the_block(self):
        res = await remove_section(_ctx(), {"page": "home", "section_id": "hero-0"})
        assert res.ok
        assert res.data["diff"]["section_order_before"] == ["hero-0", "testimonials-0"]
        assert res.data["diff"]["section_order_after"] == ["testimonials-0"]
        # The whole block travels with the proposal so undo can restore it.
        assert res.proposal["params"]["removed_section"]["type"] == "hero"
        assert res.proposal["params"]["position"] == 0

    @pytest.mark.asyncio
    async def test_apply_drops_it_from_both_the_map_and_the_order(self):
        draft = copy.deepcopy(DRAFT)
        _apply_change("remove_section", draft, {"page": "home", "section_id": "hero-0"})
        tpl = draft["templates"]["home"]
        assert "hero-0" not in tpl["sections"]
        assert tpl["order"] == ["testimonials-0"]

    @pytest.mark.asyncio
    async def test_removing_something_already_gone_is_an_error_not_a_crash(self):
        from src.application.agent.proposals import ProposalError

        draft = copy.deepcopy(DRAFT)
        with pytest.raises(ProposalError):
            _apply_change(
                "remove_section", draft, {"page": "home", "section_id": "ghost"}
            )

    @pytest.mark.asyncio
    async def test_permission_is_required(self):
        res = await remove_section(
            _ctx(allowed=False), {"page": "home", "section_id": "hero-0"}
        )
        assert res.ok is False and res.error_code == "forbidden"


def test_both_tools_are_confirm_tier_and_ask_for_themes_edit():
    for spec in (UPDATE_SECTION_SPEC, REMOVE_SECTION_SPEC):
        assert spec["risk_tier"] == RiskTier.CONFIRM
        assert spec["required_permission"] == "themes.edit"
