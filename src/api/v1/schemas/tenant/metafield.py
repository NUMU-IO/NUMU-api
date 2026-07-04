"""Metafield request/response schemas (merchant CRUD)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.entities.metafield import MetafieldOwnerType, MetafieldType

# namespace/key are the stable address a theme reads — keep them URL/handle
# safe (letters, digits, underscore, dash) so `namespace.key` is unambiguous.
_HANDLE_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_handle(v: str) -> str:
    v = v.strip()
    if not v or not _HANDLE_RE.match(v):
        raise ValueError(
            "must be non-empty and contain only letters, digits, '_' or '-'"
        )
    return v


class CreateMetafieldDefinitionRequest(BaseModel):
    """Declare a new typed metafield for a catalog resource."""

    owner_type: MetafieldOwnerType = Field(
        description="Resource the field attaches to (product/collection/page)"
    )
    namespace: str = Field(
        ..., min_length=1, max_length=64, description="Grouping namespace, e.g. 'specs'"
    )
    key: str = Field(
        ..., min_length=1, max_length=64, description="Field key, e.g. 'material'"
    )
    type: MetafieldType = Field(description="Declared value type")
    name: str = Field(
        ..., min_length=1, max_length=128, description="Human-readable display name"
    )
    description: str | None = Field(None, max_length=1000)
    is_public: bool = Field(
        True, description="Expose on the storefront detail payload for themes"
    )

    @field_validator("namespace", "key")
    @classmethod
    def _check_handle(cls, v: str) -> str:
        return _validate_handle(v)


class UpdateMetafieldDefinitionRequest(BaseModel):
    """Partial update — the ``namespace.key`` address is immutable.

    Only display metadata, declared ``type`` and ``is_public`` visibility can
    change (mutating the address would orphan existing values).
    """

    type: MetafieldType | None = Field(None)
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=1000)
    is_public: bool | None = Field(None)


class MetafieldDefinitionResponse(BaseModel):
    """Metafield definition response."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Definition UUID")
    store_id: str = Field(description="Owning store UUID")
    owner_type: MetafieldOwnerType = Field(description="Attached resource type")
    namespace: str = Field(description="Grouping namespace")
    key: str = Field(description="Field key")
    type: MetafieldType = Field(description="Declared value type")
    name: str = Field(description="Display name")
    description: str | None = Field(description="Optional description")
    is_public: bool = Field(description="Whether exposed on the storefront")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class SetMetafieldValueRequest(BaseModel):
    """Set (upsert) a metafield value on an owner.

    The definition is resolved by ``(owner_type, namespace, key)`` from the
    path/body; ``value`` is validated against that definition's declared type
    server-side, so a string, number, boolean, or JSON object are all valid
    inputs depending on the field.
    """

    namespace: str = Field(..., min_length=1, max_length=64)
    key: str = Field(..., min_length=1, max_length=64)
    value: Any = Field(description="Value, typed per the definition's declared type")

    @field_validator("namespace", "key")
    @classmethod
    def _check_handle(cls, v: str) -> str:
        return _validate_handle(v)


class MetafieldValueResponse(BaseModel):
    """Metafield value response — carries the definition address + typed value."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Value UUID")
    definition_id: str = Field(description="Owning definition UUID")
    owner_id: str = Field(description="Owner (product/collection/page) UUID")
    namespace: str = Field(description="Definition namespace")
    key: str = Field(description="Definition key")
    type: MetafieldType = Field(description="Declared value type")
    value: Any = Field(description="Value coerced to its declared type")
    raw_value: str = Field(description="Canonical stored text form")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")
