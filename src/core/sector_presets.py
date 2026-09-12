"""Sector presets — sector configures NUMU, it does not fork NUMU.

A preset is a bundle of things a merchant in a given sector would otherwise
hand-build one at a time:

  - typed field declarations (``metafield_definitions``) for the product
    information that sector needs — ISBN and author for a bookstore, roast
    and origin for a coffee roaster;
  - a starter category tree;
  - the commerce capabilities that sector normally needs turned on;
  - a recommended home-page section order for the active V3 theme.

Presets live in code rather than a table on purpose: they are platform
content, they need to be reviewed like code, and no merchant or admin
edits them. A merchant is free to edit or delete anything a preset created
afterwards — applying a preset is a one-shot seed, not a binding.

Size and colour are deliberately absent from every field list: those are
variant option axes (``products.options`` / ``product_variants``), and
duplicating them as metafields would give a product two disagreeing sizes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.entities.metafield import MetafieldOwnerType, MetafieldType


@dataclass(frozen=True)
class PresetField:
    """One typed field declaration a preset creates for the store."""

    key: str
    type: MetafieldType
    name: str
    name_ar: str
    owner_type: MetafieldOwnerType = MetafieldOwnerType.PRODUCT
    is_public: bool = True


@dataclass(frozen=True)
class PresetCategory:
    """One starter category a preset creates."""

    slug: str
    name: str
    name_ar: str


@dataclass(frozen=True)
class SectorPreset:
    """A sector's field schema, starter categories, capabilities and layout."""

    key: str
    name: str
    name_ar: str
    description: str
    namespace: str
    fields: list[PresetField] = field(default_factory=list)
    categories: list[PresetCategory] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    home_sections: list[str] = field(default_factory=list)


_TEXT = MetafieldType.SINGLE_LINE_TEXT
_LONG = MetafieldType.MULTI_LINE_TEXT
_NUM = MetafieldType.NUMBER


SECTOR_PRESETS: dict[str, SectorPreset] = {
    "fashion": SectorPreset(
        key="fashion",
        name="Fashion & Apparel",
        name_ar="أزياء وملابس",
        description="Clothing stores. Size and colour stay as variant options.",
        namespace="fashion",
        fields=[
            PresetField("material", _TEXT, "Material", "الخامة"),
            PresetField("fit", _TEXT, "Fit", "المقاس العام"),
            PresetField("gender", _TEXT, "Gender", "النوع"),
            PresetField(
                "care_instructions", _LONG, "Care instructions", "تعليمات العناية"
            ),
            PresetField(
                "model_height_cm", _NUM, "Model height (cm)", "طول العارض (سم)"
            ),
        ],
        categories=[
            PresetCategory("women", "Women", "حريمي"),
            PresetCategory("men", "Men", "رجالي"),
            PresetCategory("kids", "Kids", "أطفال"),
            PresetCategory("new-arrivals", "New arrivals", "وصل حديثاً"),
        ],
        capabilities=["variants", "inventory", "shipping"],
        home_sections=[
            "hero",
            "categories",
            "featured_collection",
            "product_grid",
            "image_with_text",
            "newsletter",
        ],
    ),
    "bookstore": SectorPreset(
        key="bookstore",
        name="Bookstore",
        name_ar="مكتبة",
        description="Books and publications, with bibliographic fields.",
        namespace="books",
        fields=[
            PresetField("isbn", _TEXT, "ISBN", "الترقيم الدولي"),
            PresetField("author", _TEXT, "Author", "المؤلف"),
            PresetField("publisher", _TEXT, "Publisher", "الناشر"),
            PresetField("language", _TEXT, "Language", "اللغة"),
            PresetField("pages", _NUM, "Pages", "عدد الصفحات"),
            PresetField(
                "publication_date",
                MetafieldType.DATE,
                "Publication date",
                "تاريخ النشر",
            ),
            PresetField("edition_year", _NUM, "Edition year", "سنة الإصدار"),
            # A used bookshop sells a COPY, not a title: where it sits and what
            # shape it is in are part of the offer, and a bookseller's own
            # recommendation is the thing that actually sells it. The rating
            # pair is a fallback for stores with no review history yet — a
            # store with reviews should show the real average instead.
            PresetField("shelf_location", _TEXT, "Shelf location", "مكان الرف"),
            PresetField("staff_pick", _LONG, "Staff pick note", "ملاحظة اختيار الفريق"),
            PresetField("staff_pick_by", _TEXT, "Recommended by", "ترشيح من"),
            PresetField("rating", _NUM, "Rating out of 5", "التقييم من ٥"),
            PresetField("rating_count", _NUM, "Number of ratings", "عدد التقييمات"),
        ],
        categories=[
            PresetCategory("islamic", "Islamic", "إسلامي"),
            PresetCategory("literature", "Literature", "أدب"),
            PresetCategory("children", "Children", "أطفال"),
            PresetCategory("new-releases", "New releases", "إصدارات جديدة"),
        ],
        capabilities=["variants", "inventory", "shipping", "digital_delivery"],
        home_sections=[
            "hero",
            "featured_collection",
            "collection_list",
            "product_grid",
            "rich_text",
            "newsletter",
        ],
    ),
    "coffee": SectorPreset(
        key="coffee",
        name="Coffee & Roastery",
        name_ar="قهوة ومحمصة",
        description="Roasters and coffee shops. Grind and weight stay as variant options.",
        namespace="coffee",
        fields=[
            PresetField("roast", _TEXT, "Roast level", "درجة التحميص"),
            PresetField("origin", _TEXT, "Origin", "المنشأ"),
            PresetField("process", _TEXT, "Process", "طريقة المعالجة"),
            PresetField("tasting_notes", _LONG, "Tasting notes", "ملاحظات التذوق"),
            PresetField("altitude_m", _NUM, "Altitude (m)", "الارتفاع (متر)"),
        ],
        categories=[
            PresetCategory("beans", "Beans", "حبوب"),
            PresetCategory("ground", "Ground", "مطحون"),
            PresetCategory("equipment", "Equipment", "أدوات"),
        ],
        capabilities=["variants", "inventory", "shipping"],
        home_sections=[
            "hero",
            "featured_collection",
            "image_with_text",
            "product_grid",
            "testimonials",
            "newsletter",
        ],
    ),
    "accessories": SectorPreset(
        key="accessories",
        name="Accessories",
        name_ar="إكسسوارات",
        description="Bags, jewellery and small goods.",
        namespace="accessories",
        fields=[
            PresetField("material", _TEXT, "Material", "الخامة"),
            PresetField("dimensions", _TEXT, "Dimensions", "الأبعاد"),
            PresetField("compatibility", _TEXT, "Compatibility", "التوافق"),
        ],
        categories=[
            PresetCategory("bags", "Bags", "شنط"),
            PresetCategory("jewellery", "Jewellery", "مجوهرات"),
            PresetCategory("watches", "Watches", "ساعات"),
        ],
        capabilities=["variants", "inventory", "shipping"],
        home_sections=[
            "hero",
            "featured_collection",
            "product_grid",
            "image_with_text",
            "newsletter",
        ],
    ),
    "scarves": SectorPreset(
        key="scarves",
        name="Scarves & Hijabs",
        name_ar="طرح وإيشاربات",
        description="Scarves, hijabs and wraps.",
        namespace="scarves",
        fields=[
            PresetField("material", _TEXT, "Material", "الخامة"),
            PresetField("dimensions", _TEXT, "Dimensions", "الأبعاد"),
            PresetField("pattern", _TEXT, "Pattern", "النقشة"),
            PresetField("opacity", _TEXT, "Opacity", "درجة الشفافية"),
        ],
        categories=[
            PresetCategory("chiffon", "Chiffon", "شيفون"),
            PresetCategory("cotton", "Cotton", "قطن"),
            PresetCategory("instant", "Instant", "جاهزة"),
        ],
        capabilities=["variants", "inventory", "shipping"],
        home_sections=[
            "hero",
            "featured_collection",
            "product_grid",
            "image_with_text",
            "newsletter",
        ],
    ),
    "electronics": SectorPreset(
        key="electronics",
        name="Electronics",
        name_ar="إلكترونيات",
        description="Devices and gadgets, with warranty and spec fields.",
        namespace="electronics",
        fields=[
            PresetField("model_number", _TEXT, "Model number", "رقم الموديل"),
            PresetField("warranty_months", _NUM, "Warranty (months)", "الضمان (شهور)"),
            PresetField("power", _TEXT, "Power", "الطاقة"),
            PresetField("specifications", _LONG, "Specifications", "المواصفات"),
        ],
        categories=[
            PresetCategory("phones", "Phones", "موبايلات"),
            PresetCategory("audio", "Audio", "صوتيات"),
            PresetCategory("accessories", "Accessories", "إكسسوارات"),
        ],
        capabilities=["variants", "inventory", "shipping"],
        home_sections=[
            "hero",
            "categories",
            "featured_collection",
            "product_grid",
            "newsletter",
        ],
    ),
}


def get_preset(key: str) -> SectorPreset | None:
    """Return the preset for ``key``, or None when it is not a known sector."""
    return SECTOR_PRESETS.get(key)
