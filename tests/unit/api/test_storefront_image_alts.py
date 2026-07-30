"""Regression tests for storefront product image-alt serialisation.

Guarding a production outage. `image_alts` exists on the `Product` ENTITY as a
method and on `ProductDTO` as a dict field, and the storefront routes in
`routes/storefront/public.py` are fed from both sources:

  * repositories return entities        -> `image_alts` is callable
  * use cases return `ProductDTO`       -> `image_alts` is a dict

The feature shipped calling `product.image_alts()` at all four call sites. Two
of them (``browse_products`` and ``get_related_products``) serialise DTOs, so
they raised ``AttributeError: 'ProductDTO' object has no attribute
'image_alts'`` -> HTTP 500. Every collection page, product listing, search
result and featured-collection section on every live storefront rendered
"0 products", because the callers swallow a failed fetch into an empty list.
The two entity-fed handlers immediately next to them worked, which is why it
read as correct in review.

The fix is the `_image_alts` accessor: one place that tolerates both shapes.
These tests pin the accessor's behaviour AND the source-level invariant that no
handler bypasses it — the second is what stops the bug from being reintroduced
by the next route that happens to be DTO-fed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.api.v1.routes.storefront.public import _image_alts
from src.application.dto.product import ProductDTO

PUBLIC_ROUTES = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "api"
    / "v1"
    / "routes"
    / "storefront"
    / "public.py"
)

ALTS = {"https://cdn.example/a.webp": "A navy pleated scarf, folded"}


class _EntityShaped:
    """What a repository hands back: `image_alts` is a bound method."""

    def image_alts(self) -> dict[str, str]:
        return dict(ALTS)


class _DtoShaped:
    """What a use case hands back: `image_alts` is a plain dict field."""

    image_alts = dict(ALTS)


class _NoAlts:
    """A product from a source that never populated alts at all."""


class TestImageAltsAccessor:
    """`_image_alts` must be indifferent to which shape it is handed."""

    def test_reads_the_entity_method(self):
        assert _image_alts(_EntityShaped()) == ALTS

    def test_reads_the_dto_field(self):
        # The case that 500'd in production.
        assert _image_alts(_DtoShaped()) == ALTS

    def test_both_shapes_agree(self):
        # The invariant that matters: a route's output must not depend on
        # whether it was wired to a repository or to a use case.
        assert _image_alts(_EntityShaped()) == _image_alts(_DtoShaped())

    def test_missing_attribute_is_empty_not_an_error(self):
        # Degrade to "no alt text", never to a 500. A missing alt costs SEO;
        # an exception costs the entire catalogue.
        assert _image_alts(_NoAlts()) == {}

    @pytest.mark.parametrize("junk", [None, "", [], 0, ["alt"], object()])
    def test_non_dict_values_are_empty_not_an_error(self, junk):
        class _Junk:
            image_alts = junk

        assert _image_alts(_Junk()) == {}

    def test_callable_returning_junk_is_empty_not_an_error(self):
        class _JunkMethod:
            def image_alts(self):
                return None

        assert _image_alts(_JunkMethod()) == {}


class TestProductDtoCarriesAlts:
    """The DTO must carry alts, or listing endpoints silently lose them."""

    def test_dto_declares_the_field(self):
        assert "image_alts" in ProductDTO.__dataclass_fields__

    def test_field_defaults_to_empty_dict_per_instance(self):
        # A shared mutable default would leak one product's alt text onto
        # every other product built without one.
        a = ProductDTO.__dataclass_fields__["image_alts"].default_factory()
        b = ProductDTO.__dataclass_fields__["image_alts"].default_factory()
        assert a == {} and b == {}
        a["x"] = "y"
        assert b == {}

    def test_from_entity_copies_the_entity_alts(self):
        # Without this the fix would be "no 500, but the merchant's alt text
        # never reaches a collection page" — a silent regression of the
        # feature rather than a loud one.
        from src.application.dto.product import ProductDTO as _DTO

        source = (
            _DTO.from_entity.__func__
            if hasattr(_DTO.from_entity, "__func__")
            else _DTO.from_entity
        )
        src = source.__code__.co_consts
        assert any(
            isinstance(c, str) and c == "image_alts" for c in src
        ) or "image_alts" in str(src), (
            "ProductDTO.from_entity must populate image_alts from the entity"
        )


class TestNoHandlerBypassesTheAccessor:
    """Source-level guard: the reason this outage was possible at all.

    A new storefront handler that copies its neighbour's
    `product.image_alts()` reintroduces the 500 the moment it is wired to a
    use case instead of a repository. Nothing about the call site reveals
    which one it is, so the safety has to live here.
    """

    def test_no_direct_image_alts_call_in_storefront_routes(self):
        source = PUBLIC_ROUTES.read_text(encoding="utf-8")
        # Strip docstrings/comments so the explanatory prose in `_image_alts`
        # (which quotes the broken call on purpose) doesn't trip the check.
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        code = re.sub(r"#.*", "", code)
        offenders = re.findall(r"\w+\.image_alts\s*\(", code)
        assert not offenders, (
            "Call `_image_alts(product)` instead of `product.image_alts()` — "
            "the latter is an AttributeError (HTTP 500) for every handler fed "
            f"a ProductDTO. Found: {offenders}"
        )

    def test_every_alt_serialisation_uses_the_accessor(self):
        source = PUBLIC_ROUTES.read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", source, flags=re.S)
        # Every place that emits an image_alts value must do so via the
        # accessor. Counts both the dict-literal and kwarg spellings.
        emits = re.findall(r"image_alts[\"']?\s*[:=]\s*([^,\n]+)", code)
        assert emits, "expected at least one image_alts serialisation site"
        for expr in emits:
            assert "_image_alts(" in expr, (
                f"image_alts serialised via {expr.strip()!r}; use "
                "_image_alts(product) so DTO- and entity-fed routes agree"
            )
