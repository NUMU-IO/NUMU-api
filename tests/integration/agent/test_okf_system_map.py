"""OKF layer — the platform map + graph integrity + doc-level structure.

The system map is what orients the agent every turn (the "OKF" half of the
OKF+RAG hybrid), so it must render, name real tools, and stay consistent with the
authored taxonomy.
"""

from __future__ import annotations

from src.application.agent.knowledge.corpus_loader import (
    load_areas,
    load_authored_corpus,
)
from src.application.agent.knowledge.system_map import build_system_map

# Tool names the agent actually exposes — the map must not invent tools.
_REAL_TOOLS = {
    "search_knowledge",
    "recommend_growth",
    "get_orders",
    "get_products",
    "get_store_summary",
    "get_theme_config",
    "add_theme_section",
    "update_theme_setting",
}


def test_system_map_renders_with_areas_and_real_tools():
    en = build_system_map("en")
    assert "NUMU platform map" in en
    # A few known areas appear.
    for label in ("Payments", "Orders", "Themes", "Growth"):
        assert label in en
    # Arabic renders with Arabic labels.
    ar = build_system_map("ar")
    assert "المدفوعات" in ar


def test_area_graph_is_internally_consistent():
    areas = load_areas()
    keys = {a.key for a in areas}
    for a in areas:
        for r in a.related_areas:
            assert r in keys, f"area '{a.key}' relates to unknown area '{r}'"
        for t in a.tools:
            assert t in _REAL_TOOLS, f"area '{a.key}' names unknown tool '{t}'"


def test_loader_parses_okf_doc_fields():
    docs = {d.source: d for d in load_authored_corpus()}
    paymob = docs["numu-corpus/payments/paymob"]
    assert paymob.doc_type == "howto"
    assert "numu-corpus/payments/cod" in paymob.related
    assert paymob.maps_to_endpoint == "/stores/{id}/payments"

    theme = docs["numu-corpus/themes/editor-v3"]
    assert theme.maps_to_tool == "get_theme_config"
