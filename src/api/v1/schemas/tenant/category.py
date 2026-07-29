"""Category Pydantic schemas for store management."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateCategoryRequest(BaseModel):
    """Create category request schema."""

    name: str = Field(..., min_length=1, max_length=255, description="Category name")
    slug: str | None = Field(
        None,
        max_length=255,
        description="URL slug (auto-generated from name if omitted)",
    )
    description: str | None = Field(None, description="Category description")
    image_url: str | None = Field(
        None, max_length=500, description="Category image URL"
    )
    parent_id: str | None = Field(None, description="Parent category UUID for nesting")
    position: int = Field(0, ge=0, description="Sort position (lower = first)")
    is_active: bool = Field(True, description="Whether the category is visible")
    seo_title: str | None = Field(
        default=None,
        max_length=70,
        description="SEO page title for the collection page.",
    )
    robots_noindex: bool = Field(
        default=False,
        description="Keep this page out of search results.",
    )
    canonical_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Absolute URL this page should credit as the original.",
    )
    sitemap_exclude: bool = Field(
        default=False,
        description="Leave this page out of sitemap.xml.",
    )
    seo_description: str | None = Field(
        default=None,
        max_length=160,
        description="SEO meta description for the collection page.",
    )
    social_image_url: str | None = Field(
        default=None,
        max_length=2048,
        description="OG/Twitter card image for the collection page.",
    )
    template_suffix: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"^[a-z0-9][a-z0-9-]{0,31}$",
        description="Alternate template variant suffix (Shopify-style); null = base template.",
    )
    extra_data: dict[str, Any] | None = Field(
        None, description="Extra metadata (e.g. name_ar, description_ar)"
    )


class UpdateCategoryRequest(BaseModel):
    """Update category request schema."""

    name: str | None = Field(
        None, min_length=1, max_length=255, description="Category name"
    )
    slug: str | None = Field(None, max_length=255, description="URL slug")
    description: str | None = Field(None, description="Category description")
    image_url: str | None = Field(
        None, max_length=500, description="Category image URL"
    )
    parent_id: str | None = Field(None, description="Parent category UUID")
    position: int | None = Field(None, ge=0, description="Sort position")
    is_active: bool | None = Field(None, description="Whether the category is visible")
    seo_title: str | None = Field(
        default=None,
        max_length=70,
        description="SEO page title for the collection page.",
    )
    robots_noindex: bool = Field(
        default=False,
        description="Keep this page out of search results.",
    )
    canonical_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Absolute URL this page should credit as the original.",
    )
    sitemap_exclude: bool = Field(
        default=False,
        description="Leave this page out of sitemap.xml.",
    )
    seo_description: str | None = Field(
        default=None,
        max_length=160,
        description="SEO meta description for the collection page.",
    )
    social_image_url: str | None = Field(
        default=None,
        max_length=2048,
        description="OG/Twitter card image for the collection page.",
    )
    template_suffix: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"^[a-z0-9][a-z0-9-]{0,31}$",
        description="Alternate template variant suffix (Shopify-style); null = base template.",
    )
    extra_data: dict[str, Any] | None = Field(
        None, description="Extra metadata (e.g. name_ar, description_ar)"
    )


class CategoryResponse(BaseModel):
    """Category response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Category UUID")
    store_id: str = Field(description="Owning store UUID")
    name: str = Field(description="Category name")
    slug: str = Field(description="URL slug")
    description: str | None = Field(description="Category description")
    image_url: str | None = Field(description="Category image URL")
    parent_id: str | None = Field(description="Parent category UUID")
    position: int = Field(description="Sort position")
    is_active: bool = Field(description="Whether the category is visible")
    seo_title: str | None = Field(default=None, description="SEO page title override.")
    robots_noindex: bool = Field(
        default=False,
        description="Keep this page out of search results.",
    )
    canonical_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Absolute URL this page should credit as the original.",
    )
    sitemap_exclude: bool = Field(
        default=False,
        description="Leave this page out of sitemap.xml.",
    )
    seo_description: str | None = Field(
        default=None, description="SEO meta description override."
    )
    social_image_url: str | None = Field(
        default=None, description="OG/Twitter card image."
    )
    template_suffix: str | None = Field(
        default=None,
        description="Alternate template variant suffix; null = base template.",
    )
    product_count: int = Field(description="Number of products in this category")
    extra_data: dict[str, Any] | None = Field(
        None, description="Extra metadata (e.g. name_ar, description_ar)"
    )
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")
