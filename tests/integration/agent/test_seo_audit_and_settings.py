"""SEO/GEO/AEO schema and the audit that reads it.

The audit is only useful if "complete" really means no findings and a blank
store really flags the expensive gaps, so both ends are pinned here. The
schema tests cover the cleaning that stops a model writing a shape the
storefront cannot read back.
"""

from __future__ import annotations

from src.api.v1.schemas.tenant.store_seo import (
    StoreSeoSettings,
    normalize_store_seo,
)
from src.infrastructure.agent.tools.seo_audit import _audit
from src.infrastructure.agent.tools.store_admin import WRITABLE_SETTINGS

_COMPLETE = {
    "seo_title": "Vionne — linen made in Cairo",
    "seo_description": "Hand-cut linen shirts and dresses, shipped across Egypt.",
    "social_image_url": "https://cdn.example.com/share.png",
    "google_site_verification": "token",
    "short_answer": "Vionne sells hand-cut linen clothing made in Cairo.",
    "faqs": [{"question": "Do you deliver to Aswan?", "answer": "Yes, 3-5 days."}],
    "same_as": ["https://instagram.com/vionne"],
    "contact_email": "hello@vionne.example",
    "area_served": ["Egypt"],
    "business_type": "ClothingStore",
}


def test_blank_store_flags_the_expensive_gaps():
    findings = _audit(StoreSeoSettings().model_dump(), None)
    keys = {f["fix_with_key"] for f in findings}
    # The four that actually cost traffic.
    assert {"seo_title", "seo_description", "short_answer", "faqs"} <= keys
    assert all(f["severity"] in ("high", "medium", "low") for f in findings)
    assert any(f["area"] == "aeo" for f in findings)
    assert any(f["area"] == "geo" for f in findings)


def test_configured_store_has_nothing_to_fix():
    seo = StoreSeoSettings(**_COMPLETE).model_dump()
    assert _audit(seo, "Vionne") == []


def test_every_finding_names_a_key_the_write_tool_accepts():
    """A finding the agent cannot act on is advice, not a fix."""
    for finding in _audit(StoreSeoSettings().model_dump(), None):
        assert finding["fix_with_key"] in WRITABLE_SETTINGS, finding


def test_blocked_indexing_is_reported_not_assumed_deliberate():
    seo = StoreSeoSettings(**_COMPLETE, robots_indexing_enabled=False).model_dump()
    findings = _audit(seo, "Vionne")
    assert [f["fix_with_key"] for f in findings] == ["robots_indexing_enabled"]
    assert findings[0]["severity"] == "high"


def test_ai_crawler_block_is_geo_not_seo():
    seo = StoreSeoSettings(**_COMPLETE, ai_crawlers_allowed=False).model_dump()
    findings = _audit(seo, "Vionne")
    assert [(f["area"], f["fix_with_key"]) for f in findings] == [
        ("geo", "ai_crawlers_allowed")
    ]


def test_profiles_are_deduped_trimmed_and_absolute():
    m = StoreSeoSettings(
        same_as=[
            "https://instagram.com/x",
            "  https://instagram.com/x  ",
            "@handle",
            "",
        ],
        area_served=[" Egypt ", "Egypt", "Sudan"],
    )
    assert m.same_as == ["https://instagram.com/x"]
    assert m.area_served == ["Egypt", "Sudan"]


def test_defaults_favour_being_found():
    """A store nobody has configured should still be indexable and citable."""
    m = StoreSeoSettings()
    assert m.robots_indexing_enabled is True
    assert m.ai_crawlers_allowed is True
    assert m.llms_txt_enabled is True


def test_training_is_the_one_default_that_says_no():
    """Being read to answer a question and being trained on are different
    bargains. The merchant gets nothing back for the second, so silence
    means no — the opposite of every other default here."""
    assert StoreSeoSettings().ai_training_allowed is False


def test_opting_into_training_is_surfaced_not_buried():
    seo = StoreSeoSettings(**_COMPLETE, ai_training_allowed=True).model_dump()
    findings = _audit(seo, "Vionne")
    assert [f["fix_with_key"] for f in findings] == ["ai_training_allowed"]


def test_normalize_survives_a_malformed_blob():
    """A bad settings blob must degrade, never break the storefront payload."""
    assert normalize_store_seo("not a dict")["robots_indexing_enabled"] is True
    assert normalize_store_seo({"seo": {"faqs": "nonsense"}})["faqs"] == []
