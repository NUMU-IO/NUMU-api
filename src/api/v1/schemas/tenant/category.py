"""Category Pydantic schemas for store management."""

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
    product_count: int = Field(description="Number of products in this category")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")
