"""Starter copy for a new store, from its name and category.

A new store used to open on the theme's sample sentence ("a curated edit of
clothing and accessories") whatever it sold. This fills the hero line and the
store description from short per-category templates, in the store language.
Only empty fields are written: anything the merchant typed is left alone.
"""

from __future__ import annotations

from typing import Any

# category -> (hero line ar, hero line en, about ar, about en). {name} = store.
_COPY: dict[str, tuple[str, str, str, str]] = {
    "fashion": (
        "هدوم مختارة بعناية — اطلب واستلم وادفع عند الاستلام.",
        "Clothes picked with care. Order now and pay on delivery.",
        "{name} — محل هدوم أونلاين. بنختار كل قطعة بعناية وبنوصّل لحد باب البيت.",
        "{name} is an online clothing shop. Every piece is picked with care and delivered to your door.",
    ),
    "electronics": (
        "إلكترونيات أصلية بأسعار واضحة — والدفع عند الاستلام.",
        "Genuine electronics at clear prices, with cash on delivery.",
        "{name} — بنبيع إلكترونيات أصلية ونوصّلها لحد عندك.",
        "{name} sells genuine electronics and delivers them to you.",
    ),
    "beauty": (
        "منتجات عناية وتجميل أصلية — توصيل لحد البيت.",
        "Genuine beauty and care products, delivered to your door.",
        "{name} — منتجات عناية وتجميل أصلية مختارة بعناية.",
        "{name} offers genuine beauty and care products, picked with care.",
    ),
    "home": (
        "حاجات حلوة للبيت — اطلب واستلم وادفع عند الاستلام.",
        "Good things for your home. Order now and pay on delivery.",
        "{name} — كل اللي يحلّي بيتك في مكان واحد.",
        "{name} brings together good things for your home.",
    ),
    "food": (
        "أكل ومشروبات طازة — اطلب والتوصيل علينا.",
        "Fresh food and drinks, delivered.",
        "{name} — أكل ومشروبات بنحضّرها بعناية ونوصّلها لحد عندك.",
        "{name} prepares food and drinks with care and delivers them to you.",
    ),
    "accessories": (
        "إكسسوارات تكمّل اللوك — والدفع عند الاستلام.",
        "Accessories that finish the look, with cash on delivery.",
        "{name} — إكسسوارات مختارة بعناية لكل يوم ولكل مناسبة.",
        "{name} offers accessories picked with care, for every day and every occasion.",
    ),
    "books": (
        "كتب تستاهل تتقري — توصيل لحد البيت.",
        "Books worth reading, delivered to your door.",
        "{name} — مكتبة أونلاين بتوصّل الكتب لحد عندك.",
        "{name} is an online bookshop that delivers to your door.",
    ),
    "handmade": (
        "شغل يدوي معمول بحب — كل قطعة ليها حكاية.",
        "Handmade with care. Every piece has a story.",
        "{name} — شغل يدوي معمول بإيدينا، قطعة قطعة.",
        "{name} makes every piece by hand, one at a time.",
    ),
    "other": (
        "اطلب أونلاين وادفع عند الاستلام.",
        "Order online and pay on delivery.",
        "{name} — بنوصّل لحد باب البيت.",
        "{name} delivers to your door.",
    ),
}

# The hero line lives under different keys depending on the theme.
_HERO_LINE_KEYS = ("subtitle", "body")


def starter_copy(category: str, store_name: str, language: str) -> tuple[str, str]:
    """(hero line, about text) for this category, in ``language``."""
    hero_ar, hero_en, about_ar, about_en = _COPY.get(category, _COPY["other"])
    if language == "en":
        return hero_en, about_en.format(name=store_name)
    return hero_ar, about_ar.format(name=store_name)


def _setting_ids(section_schemas: Any, section_type: str) -> set[str]:
    schema = None
    if isinstance(section_schemas, dict):
        schema = section_schemas.get(section_type)
    elif isinstance(section_schemas, list):
        schema = next(
            (
                s
                for s in section_schemas
                if isinstance(s, dict) and s.get("type") == section_type
            ),
            None,
        )
    settings = (schema or {}).get("settings") or []
    return {
        s["id"]
        for s in settings
        if isinstance(s, dict) and isinstance(s.get("id"), str)
    }


def fill_hero_line(customization: dict | None, section_schemas: Any, line: str) -> bool:
    """Write ``line`` into the home hero's empty subtitle-like setting.

    Returns whether anything changed. Sections may be a dict keyed by id or a
    list, as both shapes exist in stored customizations.
    """
    if not isinstance(customization, dict):
        return False
    home = ((customization.get("templates") or {}).get("home")) or {}
    sections = home.get("sections") or {}
    instances = sections.values() if isinstance(sections, dict) else sections
    for section in instances:
        if not isinstance(section, dict) or "hero" not in str(section.get("type", "")):
            continue
        declared = _setting_ids(section_schemas, section["type"])
        key = next((k for k in _HERO_LINE_KEYS if k in declared), None)
        if key is None:
            return False
        settings = section.setdefault("settings", {})
        if settings.get(key):
            return False
        settings[key] = line
        return True
    return False
