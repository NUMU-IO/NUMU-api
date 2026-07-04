"""Unit tests for metafield entities + schema validation.

Covers the typed-value contract (serialize/coerce roundtrip + rejection of
mistyped values) and the definition schema validation (type/owner_type enums,
namespace/key handle rules) that back the metafields foundation.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.v1.schemas.tenant.metafield import (
    CreateMetafieldDefinitionRequest,
    UpdateMetafieldDefinitionRequest,
)
from src.core.entities.metafield import (
    MetafieldDefinition,
    MetafieldOwnerType,
    MetafieldType,
    coerce_metafield_value,
    serialize_metafield_value,
)


class TestSerializeMetafieldValue:
    """Write-path validation + canonicalization per declared type."""

    def test_number_accepts_int_float_and_numeric_string(self):
        assert serialize_metafield_value(MetafieldType.NUMBER, 42) == "42"
        assert serialize_metafield_value(MetafieldType.NUMBER, "3.14") == "3.14"

    def test_number_rejects_non_numeric_and_bool(self):
        with pytest.raises(ValueError):
            serialize_metafield_value(MetafieldType.NUMBER, "abc")
        with pytest.raises(ValueError):
            serialize_metafield_value(MetafieldType.NUMBER, True)

    def test_boolean_canonicalizes(self):
        assert serialize_metafield_value(MetafieldType.BOOLEAN, True) == "true"
        assert serialize_metafield_value(MetafieldType.BOOLEAN, "False") == "false"
        with pytest.raises(ValueError):
            serialize_metafield_value(MetafieldType.BOOLEAN, "maybe")

    def test_single_line_text_rejects_newlines(self):
        assert (
            serialize_metafield_value(MetafieldType.SINGLE_LINE_TEXT, "hello")
            == "hello"
        )
        with pytest.raises(ValueError):
            serialize_metafield_value(MetafieldType.SINGLE_LINE_TEXT, "a\nb")

    def test_url_requires_scheme_or_root(self):
        assert (
            serialize_metafield_value(MetafieldType.URL, "https://x.com")
            == "https://x.com"
        )
        with pytest.raises(ValueError):
            serialize_metafield_value(MetafieldType.URL, "notaurl")

    def test_date_validates_iso(self):
        assert (
            serialize_metafield_value(MetafieldType.DATE, "2026-07-04") == "2026-07-04"
        )
        with pytest.raises(ValueError):
            serialize_metafield_value(MetafieldType.DATE, "07/04/2026")

    def test_json_accepts_object_and_valid_string(self):
        assert serialize_metafield_value(MetafieldType.JSON, {"a": 1}) == '{"a": 1}'
        assert serialize_metafield_value(MetafieldType.JSON, "[1, 2]") == "[1, 2]"
        with pytest.raises(ValueError):
            serialize_metafield_value(MetafieldType.JSON, "{not json}")

    def test_null_always_rejected(self):
        with pytest.raises(ValueError):
            serialize_metafield_value(MetafieldType.SINGLE_LINE_TEXT, None)


class TestCoerceMetafieldValue:
    """Read-path typing — the inverse of serialize."""

    def test_number_roundtrip(self):
        raw = serialize_metafield_value(MetafieldType.NUMBER, 10)
        assert coerce_metafield_value(MetafieldType.NUMBER, raw) == 10
        assert isinstance(coerce_metafield_value(MetafieldType.NUMBER, "2.5"), float)

    def test_boolean_roundtrip(self):
        assert coerce_metafield_value(MetafieldType.BOOLEAN, "true") is True
        assert coerce_metafield_value(MetafieldType.BOOLEAN, "false") is False

    def test_json_roundtrip(self):
        raw = serialize_metafield_value(MetafieldType.JSON, {"k": [1, 2]})
        assert coerce_metafield_value(MetafieldType.JSON, raw) == {"k": [1, 2]}

    def test_none_stays_none(self):
        assert coerce_metafield_value(MetafieldType.NUMBER, None) is None


class TestMetafieldDefinitionEntity:
    """The entity carries validated StrEnum types."""

    def test_valid_definition(self):
        d = MetafieldDefinition(
            store_id=uuid4(),
            owner_type=MetafieldOwnerType.PRODUCT,
            namespace="specs",
            key="material",
            type=MetafieldType.SINGLE_LINE_TEXT,
            name="Material",
        )
        assert d.owner_type is MetafieldOwnerType.PRODUCT
        assert d.type is MetafieldType.SINGLE_LINE_TEXT
        assert d.is_public is True

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            MetafieldDefinition(
                store_id=uuid4(),
                owner_type=MetafieldOwnerType.PRODUCT,
                namespace="specs",
                key="material",
                type="not_a_type",
                name="Material",
            )


class TestCreateDefinitionSchema:
    """Request schema validation — enums + handle rules."""

    def test_valid_request(self):
        req = CreateMetafieldDefinitionRequest(
            owner_type="collection",
            namespace="care",
            key="wash_temp",
            type="number",
            name="Wash Temperature",
        )
        assert req.owner_type is MetafieldOwnerType.COLLECTION
        assert req.type is MetafieldType.NUMBER

    def test_invalid_owner_type_rejected(self):
        with pytest.raises(ValidationError):
            CreateMetafieldDefinitionRequest(
                owner_type="variant",
                namespace="care",
                key="wash_temp",
                type="number",
                name="Wash Temperature",
            )

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            CreateMetafieldDefinitionRequest(
                owner_type="product",
                namespace="care",
                key="wash_temp",
                type="rich_text",
                name="Wash Temperature",
            )

    @pytest.mark.parametrize("bad", ["has space", "dot.key", "sym$", ""])
    def test_namespace_key_reject_bad_handles(self, bad):
        with pytest.raises(ValidationError):
            CreateMetafieldDefinitionRequest(
                owner_type="product",
                namespace=bad,
                key="ok",
                type="single_line_text",
                name="X",
            )

    def test_update_schema_all_optional(self):
        # An empty update is valid (partial update — no fields required).
        req = UpdateMetafieldDefinitionRequest()
        assert req.type is None
        assert req.name is None
