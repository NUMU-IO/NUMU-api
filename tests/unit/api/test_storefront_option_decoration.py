"""Regression tests for storefront option-axis decoration.

Guarding a live production regression on two stores. Colour hexes, per-value
images and Arabic axis labels are written by the hub to ``product.attributes``
(``variant_meta.axes`` today, the legacy ``variants`` blob before Wave C), while
the axis STRUCTURE lives in the canonical ``product.options`` column.

``_resolve_options_for_product`` used to return ``product.options`` verbatim as
soon as it was non-empty. From api#578 (2026-09-09) onward every product with
canonical options therefore served a bare ``{name, position, values}``: themes
fell back to painting the merchant's LABEL as a CSS colour
(``background-color:Taupe``, which paints nothing) and Arabic axis labels
silently reverted to English.

The fix merges the decoration back on by axis name. These tests pin the merge,
the two storage shapes it has to tolerate, and the ``ProductDTO`` field without
which the collection-card half of the fix is dead on arrival.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.api.v1.routes.storefront.public import _resolve_options_for_product

AR_COLOR = "اللون"
AR_SIZE = "المقاس"

# The real shape of vionne's `sponge-taupe`, from the 2026-09-16 snapshot.
SPONGE_AR = ["بيج رمادي", "كافيه", "شوكولاتة"]
SPONGE_HEX = ["#c9b19b", "#9d7f72", "#582a24"]
SPONGE_LEGACY = {
    "variants": [
        {
            "name": "Color",
            "nameAr": AR_COLOR,
            "options": ["Taupe", "Cafe", "Chocolate"],
            "optionsAr": SPONGE_AR,
            "hexValues": SPONGE_HEX,
        }
    ]
}
SPONGE_CANONICAL = [
    {"name": "Color", "position": 0, "values": ["Taupe", "Cafe", "Chocolate"]}
]


def product(options=None, attributes=None):
    return SimpleNamespace(options=options or [], attributes=attributes or {})


class TestCanonicalMerge:
    def test_decoration_is_merged_onto_canonical_options(self):
        """The regression itself: canonical axes must not discard decoration."""
        (axis,) = _resolve_options_for_product(product(SPONGE_CANONICAL, SPONGE_LEGACY))
        assert axis["values"] == ["Taupe", "Cafe", "Chocolate"]
        assert axis["hex_values"] == SPONGE_HEX
        assert axis["name_ar"] == AR_COLOR
        assert axis["values_ar"] == SPONGE_AR

    def test_canonical_structure_still_wins(self):
        """Decoration is merged; it never overrides names, order or values."""
        canonical = [{"name": "Color", "position": 0, "values": ["Taupe"]}]
        attrs = {
            "variants": [
                {"name": "Color", "options": ["Taupe", "Cafe"], "hexValues": ["#111"]}
            ]
        }
        (axis,) = _resolve_options_for_product(product(canonical, attrs))
        assert axis["values"] == ["Taupe"]
        assert axis["position"] == 0

    def test_axis_name_match_ignores_case_and_surrounding_space(self):
        canonical = [{"name": "Color", "position": 0, "values": ["Red"]}]
        attrs = {"variants": [{"name": " COLOR ", "hexValues": ["#f00"]}]}
        (axis,) = _resolve_options_for_product(product(canonical, attrs))
        assert axis["hex_values"] == ["#f00"]

    def test_a_different_axis_name_does_not_fuzzy_match(self):
        """`Colour` is a different axis, not a case variant of this one."""
        canonical = [{"name": "Color", "position": 0, "values": ["Red"]}]
        attrs = {"variants": [{"name": "Colour", "hexValues": ["#f00"]}]}
        (axis,) = _resolve_options_for_product(product(canonical, attrs))
        assert "hex_values" not in axis

    def test_unmatched_axis_is_returned_untouched(self):
        canonical = [{"name": "Size", "position": 0, "values": ["M"]}]
        (axis,) = _resolve_options_for_product(product(canonical, SPONGE_LEGACY))
        assert axis == {"name": "Size", "position": 0, "values": ["M"]}


class TestStorageShapes:
    def test_variant_meta_is_read_before_legacy(self):
        """`variant_meta` is the key the hub writes today; it wins."""
        attrs = {
            "variant_meta": {
                "axes": [{"name": "Color", "hexValues": ["#aaa", "#bbb", "#ccc"]}]
            },
            **SPONGE_LEGACY,
        }
        (axis,) = _resolve_options_for_product(product(SPONGE_CANONICAL, attrs))
        assert axis["hex_values"] == ["#aaa", "#bbb", "#ccc"]

    def test_all_grey_variant_meta_does_not_shadow_real_legacy_hexes(self):
        """Live `sponge-taupe`: grey `variant_meta` sitting over real hexes.

        `#888888` is what the hub writes when it could not resolve a colour, so
        an all-grey axis is overwritten decoration rather than a merchant's
        choice. Treating it as data would repaint a live PDP with three
        identical grey chips, which is a different bug, not a fix.
        """
        attrs = {
            "variant_meta": {
                "axes": [
                    {"name": "Color", "hexValues": ["#888888", "#888888", "#888888"]}
                ]
            },
            **SPONGE_LEGACY,
        }
        (axis,) = _resolve_options_for_product(product(SPONGE_CANONICAL, attrs))
        assert axis["hex_values"] == SPONGE_HEX

    def test_decoration_merges_per_key_across_sources(self):
        """A source holding only Arabic must not shadow another's hexes."""
        attrs = {
            "variant_meta": {"axes": [{"name": "Color", "nameAr": AR_COLOR}]},
            **SPONGE_LEGACY,
        }
        (axis,) = _resolve_options_for_product(product(SPONGE_CANONICAL, attrs))
        assert axis["name_ar"] == AR_COLOR
        assert axis["hex_values"] == SPONGE_HEX

    def test_value_keyed_dict_is_realigned_to_value_order(self):
        """Themes index `hex_values[i]`, so a dict must not pass through."""
        attrs = {
            "variants": [
                {
                    "name": "Color",
                    "hexValues": {"Chocolate": "#582a24", "Taupe": "#c9b19b"},
                }
            ]
        }
        (axis,) = _resolve_options_for_product(product(SPONGE_CANONICAL, attrs))
        assert axis["hex_values"] == ["#c9b19b", None, "#582a24"]

    def test_empty_decoration_keys_are_omitted_not_emitted_null(self):
        attrs = {"variants": [{"name": "Color", "hexValues": [], "optionsAr": []}]}
        (axis,) = _resolve_options_for_product(product(SPONGE_CANONICAL, attrs))
        assert "hex_values" not in axis
        assert "values_ar" not in axis

    def test_image_values_are_merged_like_hexes(self):
        """The rabbit path carries `imageValues` on all 14 of its products."""
        attrs = {
            "variants": [
                {"name": "Color", "imageValues": ["https://cdn/a.webp", None, ""]}
            ]
        }
        (axis,) = _resolve_options_for_product(product(SPONGE_CANONICAL, attrs))
        assert axis["image_values"] == ["https://cdn/a.webp", None, ""]


class TestMultiAxis:
    """No multi-axis product exists on either live store, so this is the only
    place the behaviour is exercised at all."""

    CANONICAL = [
        {"name": "Color", "position": 0, "values": ["Red", "Blue"]},
        {"name": "Size", "position": 1, "values": ["S", "M"]},
    ]
    ATTRS = {
        "variants": [
            {"name": "Size", "nameAr": AR_SIZE, "options": ["S", "M"]},
            {"name": "Color", "hexValues": ["#f00", "#00f"], "nameAr": AR_COLOR},
        ]
    }

    def test_each_axis_gets_its_own_decoration(self):
        color, size = _resolve_options_for_product(product(self.CANONICAL, self.ATTRS))
        assert color["name"] == "Color"
        assert color["position"] == 0
        assert color["hex_values"] == ["#f00", "#00f"]
        assert color["name_ar"] == AR_COLOR
        assert "hex_values" not in size
        assert size["name_ar"] == AR_SIZE


class TestLegacyDerivation:
    """The rabbit path: no canonical options, axes derived from attributes."""

    def test_axes_are_derived_when_canonical_options_are_empty(self):
        (axis,) = _resolve_options_for_product(product([], SPONGE_LEGACY))
        assert axis["name"] == "Color"
        assert axis["position"] == 0
        assert axis["values"] == ["Taupe", "Cafe", "Chocolate"]
        assert axis["hex_values"] == SPONGE_HEX
        assert axis["name_ar"] == AR_COLOR

    def test_an_axis_in_both_sources_is_not_duplicated(self):
        attrs = {
            "variant_meta": {
                "axes": [{"name": "Color", "values": ["Taupe"], "hexValues": ["#aaa"]}]
            },
            **SPONGE_LEGACY,
        }
        axes = _resolve_options_for_product(product([], attrs))
        assert [a["name"] for a in axes] == ["Color"]
        assert axes[0]["hex_values"] == ["#aaa"]

    @pytest.mark.parametrize(
        "attributes",
        [
            {},
            {"variants": None},
            {"variants": "not-a-list"},
            {"variants": [{"name": "Color"}]},
            {"variants": [{"options": ["Red"]}]},
            {"variant_meta": "not-a-dict"},
        ],
    )
    def test_unusable_attributes_yield_no_axes(self, attributes):
        assert _resolve_options_for_product(product([], attributes)) == []

    def test_missing_attributes_attribute_is_tolerated(self):
        assert _resolve_options_for_product(SimpleNamespace(options=[])) == []

    def test_non_dict_entries_are_skipped(self):
        resolved = _resolve_options_for_product(
            product(["not-a-dict"], {"variants": ["also-not"]})
        )
        assert resolved == []


class TestDtoCarriesOptions:
    """Without this the collection-card half of the fix is dead on arrival.

    `browse_products` serialises ProductDTO, not entities, which is the same
    split that produced the `image_alts` outage. The DTO carried no `options`,
    so `_resolve_options_for_product` saw no canonical axes on a collection
    card and fell back to `attributes` -- empty on any product whose options
    were only ever entered in the SKU-tracked editor.
    """

    @staticmethod
    def entity(options):
        model = MagicMock()
        model.options = options
        model.related_product_ids = []
        return model

    def test_from_entity_maps_options(self):
        from src.application.dto.product import ProductDTO

        dto = ProductDTO.from_entity(self.entity(SPONGE_CANONICAL))
        assert dto.options == SPONGE_CANONICAL

    def test_from_entity_defaults_options_to_empty_list(self):
        from src.application.dto.product import ProductDTO

        assert ProductDTO.from_entity(self.entity(None)).options == []


def test_both_list_routes_serialise_options():
    """Source-level invariant: neither LIST handler may skip the resolver.

    Both hand-build their own dict rather than sharing a serialiser, so a field
    is easy to add to one and forget in the other -- which is exactly how
    `options` came to be missing from both.
    """
    import inspect

    from src.api.v1.routes.storefront import public

    for handler in (public.browse_products, public.browse_products_cursor):
        source = inspect.getsource(handler)
        assert "_resolve_options_for_product(product)" in source, handler.__name__


class TestGreySentinel:
    """`#888888` is the hub's "I could not resolve a colour" marker, not a colour.

    `ProductEditor.tsx` writes `defaultHexForName(opt)` into every hexValues
    position on every save, and that function ends in `|| "#888888"`. The hub's
    own real grey is `#737373`, so the sentinel is unambiguous. Shipping it
    would reproduce the documented category failure where "Navy Heather falls
    back to a default gray" and shoppers report it as wrong colours.
    """

    def test_a_mixed_axis_keeps_real_hexes_and_nulls_the_sentinel(self):
        attrs = {
            "variants": [
                {
                    "name": "Color",
                    "options": ["Red", "Blue", "Ecru"],
                    "hexValues": ["#e11d48", "#2563eb", "#888888"],
                }
            ]
        }
        canonical = [
            {"name": "Color", "position": 0, "values": ["Red", "Blue", "Ecru"]}
        ]
        (axis,) = _resolve_options_for_product(product(canonical, attrs))
        assert axis["hex_values"] == ["#e11d48", "#2563eb", None]

    def test_the_sentinel_is_matched_regardless_of_case_or_padding(self):
        attrs = {"variants": [{"name": "Color", "hexValues": [" #888888 ", "#888888"]}]}
        (axis,) = _resolve_options_for_product(
            product([{"name": "Color", "position": 0, "values": ["A", "B"]}], attrs)
        )
        assert "hex_values" not in axis

    def test_the_hubs_real_grey_is_not_mistaken_for_the_sentinel(self):
        # #737373 is what the hub writes when a merchant genuinely picks grey.
        attrs = {"variants": [{"name": "Color", "hexValues": ["#737373"]}]}
        (axis,) = _resolve_options_for_product(
            product([{"name": "Color", "position": 0, "values": ["Grey"]}], attrs)
        )
        assert axis["hex_values"] == ["#737373"]


class TestPerPositionMerge:
    """A partial higher-priority source must not discard a real lower one.

    Found in browser QA, not here: a product mid-migration had `variant_meta`
    holding `["#d32f2f", "#888888"]` while the legacy blob still held the
    merchant's real `["#EC0505", "#0033CC"]`. Merging whole lists meant the
    nulled sentinel discarded the real `#0033CC`, and the value fell through to
    a bilingual-lexicon guess — so a merchant who had explicitly chosen a navy
    was served a generic blue on a live product page.
    """

    CANONICAL = [{"name": "Color", "position": 0, "values": ["Red", "Blue"]}]

    def test_a_hole_left_by_the_sentinel_is_filled_from_legacy(self):
        attrs = {
            "variant_meta": {
                "axes": [{"name": "Color", "hexValues": ["#d32f2f", "#888888"]}]
            },
            "variants": [
                {"name": "Color", "hexValues": ["#EC0505", "#0033CC"]},
            ],
        }
        (axis,) = _resolve_options_for_product(product(self.CANONICAL, attrs))
        # variant_meta still wins where it HAS a real value.
        assert axis["hex_values"] == ["#d32f2f", "#0033CC"]

    def test_the_higher_priority_source_is_never_overwritten(self):
        attrs = {
            "variant_meta": {
                "axes": [{"name": "Color", "hexValues": ["#111", "#222"]}]
            },
            "variants": [{"name": "Color", "hexValues": ["#aaa", "#bbb"]}],
        }
        (axis,) = _resolve_options_for_product(product(self.CANONICAL, attrs))
        assert axis["hex_values"] == ["#111", "#222"]

    def test_the_merged_list_never_grows_past_the_axis_values(self):
        # A lower-priority source describing MORE values than this axis carries
        # would otherwise attach colours to values that do not exist here.
        canonical = [{"name": "Color", "position": 0, "values": ["Red"]}]
        attrs = {
            "variant_meta": {"axes": [{"name": "Color", "hexValues": ["#111"]}]},
            "variants": [{"name": "Color", "hexValues": ["#aaa", "#bbb", "#ccc"]}],
        }
        (axis,) = _resolve_options_for_product(product(canonical, attrs))
        assert axis["hex_values"] == ["#111"]

    def test_image_values_merge_per_position_too(self):
        attrs = {
            "variant_meta": {
                "axes": [{"name": "Color", "imageValues": ["a.webp", ""]}]
            },
            "variants": [{"name": "Color", "imageValues": ["z.webp", "b.webp"]}],
        }
        (axis,) = _resolve_options_for_product(product(self.CANONICAL, attrs))
        assert axis["image_values"] == ["a.webp", "b.webp"]

    def test_arabic_labels_merge_per_position_too(self):
        attrs = {
            "variant_meta": {"axes": [{"name": "Color", "optionsAr": ["أحمر", ""]}]},
            "variants": [{"name": "Color", "optionsAr": ["قرمزي", "أزرق"]}],
        }
        (axis,) = _resolve_options_for_product(product(self.CANONICAL, attrs))
        assert axis["values_ar"] == ["أحمر", "أزرق"]
