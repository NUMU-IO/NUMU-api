"""V3 Theme Settings Pydantic models.

Defines the canonical V3 data model for theme customization.
Separates global settings from page-specific templates,
supports blocks inside sections, and section groups for header/footer.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class BlockInstance(BaseModel):
    """A single block within a section."""

    type: str  # e.g., "button", "heading", "@app/reviews/star-rating"
    disabled: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def validate_block_type(cls, v: str) -> str:
        if v.startswith("@app/"):
            parts = v.split("/")
            if len(parts) < 3:
                raise ValueError(
                    f"@app block type must be '@app/{{slug}}/{{type}}', got '{v}'"
                )
        return v


class SectionInstance(BaseModel):
    """A section placed in a page template or section group."""

    type: str  # e.g., "hero", "featured-collection"
    disabled: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    blocks: dict[str, BlockInstance] = Field(default_factory=dict)
    block_order: list[str] = Field(default_factory=list)


class PageTemplate(BaseModel):
    """A page template defining which sections appear and in what order."""

    name: str  # e.g., "Home", "Product"
    sections: dict[str, SectionInstance] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)


class SectionGroup(BaseModel):
    """A persistent section group (header, footer).

    Section groups are rendered on every page and can contain
    multiple sections (e.g., announcement bar + header).
    """

    name: str  # e.g., "Header Group", "Footer Group"
    sections: dict[str, SectionInstance] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)


class ExternalThemeMetadata(BaseModel):
    """Metadata for BYOT (external) themes."""

    bundle_url: str
    css_url: Optional[str] = None
    settings_schema: Optional[dict[str, Any]] = None
    section_schemas: Optional[dict[str, Any]] = None
    manifest: Optional[dict[str, Any]] = None
    mode: Optional[Literal["production", "development"]] = "production"
    dev_url: Optional[str] = None  # Local Vite dev server URL


class ThemeSettingsV3(BaseModel):
    """The canonical V3 theme configuration payload.

    Written to StoreTheme.customization_v3 / draft_customization_v3.
    """

    schema_version: Literal[3] = 3
    theme_id: str  # "bazar", "modern", or a BYOT UUID
    global_settings: dict[str, Any] = Field(default_factory=dict)
    templates: dict[str, PageTemplate] = Field(default_factory=dict)
    section_groups: dict[str, SectionGroup] = Field(default_factory=dict)
    external_theme: Optional[ExternalThemeMetadata] = None
