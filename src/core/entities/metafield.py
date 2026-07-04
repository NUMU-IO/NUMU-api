"""Metafield domain entities — typed custom data on catalog resources.

Metafields replace the untyped ``product.attributes`` JSONB pass-through with
a *typed*, *namespaced* schema:

  - ``MetafieldDefinition`` declares a field once per store: an owner type
    (product / collection / page), a ``namespace.key`` address, a value
    ``type`` (single_line_text, number, boolean, json, …) and display
    metadata. It is the schema.
  - ``MetafieldValue`` is one concrete value for one owner (a specific
    product/collection/page), stored as canonical text and coerced back to
    its declared type on read.

The value is persisted as TEXT so a single column serves every type; the
declared ``MetafieldType`` on the definition is the single source of truth
for how it is (de)serialized. ``serialize_metafield_value`` validates +
canonicalizes on write; ``coerce_metafield_value`` types it back on read.
"""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class MetafieldOwnerType(StrEnum):
    """Catalog resource a metafield can be attached to."""

    PRODUCT = "product"
    COLLECTION = "collection"
    PAGE = "page"


class MetafieldType(StrEnum):
    """Typed value kinds a metafield definition can declare."""

    SINGLE_LINE_TEXT = "single_line_text"
    MULTI_LINE_TEXT = "multi_line_text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"
    URL = "url"
    DATE = "date"


class MetafieldDefinition(BaseEntity):
    """A per-store typed field declaration for a catalog resource.

    Unique per ``(store_id, owner_type, namespace, key)`` — the
    ``namespace.key`` pair is the stable address a theme reads (e.g.
    ``specs.material``). ``is_public`` gates storefront exposure; private
    definitions are merchant/back-office only.
    """

    store_id: UUID
    tenant_id: UUID | None = None
    owner_type: MetafieldOwnerType
    namespace: str
    key: str
    type: MetafieldType
    name: str
    description: str | None = None
    is_public: bool = True


class MetafieldValue(BaseEntity):
    """A concrete metafield value for one owner (product/collection/page).

    Unique per ``(definition_id, owner_id)``. ``value`` is the canonical
    text form produced by :func:`serialize_metafield_value`; use the
    owning definition's ``type`` with :func:`coerce_metafield_value` to
    read it back as its real Python type.
    """

    store_id: UUID
    tenant_id: UUID | None = None
    definition_id: UUID
    owner_id: UUID
    value: str


class ResolvedMetafield(BaseEntity):
    """A definition + value flattened for storefront exposure.

    This is what themes consume: the ``namespace.key`` address, the
    declared ``type``, and the value already coerced to its real Python
    type (number → int/float, boolean → bool, json → dict/list, …).
    """

    namespace: str
    key: str
    type: MetafieldType
    value: Any = Field(default=None)


def serialize_metafield_value(mtype: MetafieldType, value: Any) -> str:
    """Validate ``value`` against ``mtype`` and return its canonical text form.

    Raises ``ValueError`` when the value does not fit the declared type so
    the route layer can surface a 422. This is the write-path guard that
    keeps typed data actually typed.
    """
    if value is None:
        raise ValueError("Metafield value cannot be null")

    if mtype in (MetafieldType.SINGLE_LINE_TEXT, MetafieldType.MULTI_LINE_TEXT):
        if not isinstance(value, str):
            raise ValueError(f"{mtype.value} requires a string value")
        if mtype is MetafieldType.SINGLE_LINE_TEXT and "\n" in value:
            raise ValueError("single_line_text may not contain newlines")
        return value

    if mtype is MetafieldType.URL:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("url requires a non-empty string value")
        candidate = value.strip()
        if not (
            candidate.startswith("http://")
            or candidate.startswith("https://")
            or candidate.startswith("/")
        ):
            raise ValueError("url must start with http://, https:// or /")
        return candidate

    if mtype is MetafieldType.NUMBER:
        if isinstance(value, bool):
            raise ValueError("number does not accept a boolean")
        if isinstance(value, int | float):
            return repr(value)
        if isinstance(value, str):
            try:
                float(value)
            except ValueError:
                raise ValueError(f"'{value}' is not a valid number")
            return value.strip()
        raise ValueError("number requires an int, float or numeric string")

    if mtype is MetafieldType.BOOLEAN:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower()
        raise ValueError("boolean requires true/false")

    if mtype is MetafieldType.DATE:
        if not isinstance(value, str):
            raise ValueError("date requires an ISO 8601 string (YYYY-MM-DD)")
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            raise ValueError(f"'{value}' is not a valid ISO date (YYYY-MM-DD)")

    # JSON — accept any JSON-serializable object, or a JSON string.
    if isinstance(value, str):
        try:
            json.loads(value)
        except json.JSONDecodeError:
            raise ValueError("json value string is not valid JSON")
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        raise ValueError("json value is not JSON-serializable")


def coerce_metafield_value(mtype: MetafieldType, raw: str | None) -> Any:
    """Coerce canonical stored text back to its declared Python type.

    Never raises — a stored value should already be valid (written through
    :func:`serialize_metafield_value`); on the off chance of a malformed
    legacy row we fall back to returning the raw string rather than 500ing
    a storefront read.
    """
    if raw is None:
        return None

    if mtype is MetafieldType.NUMBER:
        try:
            if "." in raw or "e" in raw.lower():
                return float(raw)
            return int(raw)
        except ValueError:
            return raw

    if mtype is MetafieldType.BOOLEAN:
        return raw.strip().lower() == "true"

    if mtype is MetafieldType.JSON:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    # single_line_text, multi_line_text, url, date → the string as-is.
    return raw
