"""OKF system map — a compact, structured overview of the NUMU platform.

This is the "OKF" half of the OKF+RAG hybrid: instead of only retrieving similar
chunks, we give the agent a small map of *how the platform is organized* — its
areas, how they relate, and which tools act on each — so it can reason about the
system and pick the right tool/area before (or instead of) searching. RAG then
fills in the detail for the specific question.

Built from the authored taxonomy (`corpus/areas.json`); no merchant data.
"""

from __future__ import annotations

from src.application.agent.knowledge.corpus_loader import load_areas
from src.core.agent.knowledge import KnowledgeArea

# Cache the rendered map per locale — the taxonomy is static within a process.
_CACHE: dict[str, str] = {}


def _label(area: KnowledgeArea, locale: str) -> str:
    return area.label_ar if locale == "ar" else area.label_en


def build_system_map(locale: str = "en") -> str:
    """Return a compact platform map string for injection into the agent prompt."""
    if locale in _CACHE:
        return _CACHE[locale]

    areas = load_areas()
    by_key = {a.key: a for a in areas}
    lines: list[str] = []
    for a in areas:
        related = ", ".join(
            _label(by_key[r], locale) for r in a.related_areas if r in by_key
        )
        tools = ", ".join(a.tools)
        parts = [f"- {_label(a, locale)}: {a.description}"]
        if related:
            parts.append(f"    related: {related}")
        if tools:
            parts.append(f"    tools: {tools}")
        lines.append("\n".join(parts))

    header = (
        "NUMU platform map — the areas you help with, how they connect, and the "
        "tools that act on each. For a store-fact question use that area's data "
        "tools (get_orders, get_products, get_store_summary); for how-to/what-is "
        "use search_knowledge; when a question spans areas, consider the related "
        "ones."
    )
    rendered = header + "\n\n" + "\n".join(lines)
    _CACHE[locale] = rendered
    return rendered
