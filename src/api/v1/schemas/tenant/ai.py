"""AI description generator Pydantic schemas."""

from pydantic import BaseModel, ConfigDict, Field


class GenerateDescriptionRequest(BaseModel):
    """Request to generate bilingual AI product descriptions."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "product_name": "Egyptian Cotton Tee",
                "product_name_ar": "تيشيرت قطن مصري",
                "category": "Clothing",
                "image_url": "https://r2.numu.io/stores/abc/products/img.jpg",
                "attributes": {"material": "100% Egyptian Cotton", "color": "White"},
                "tone": "professional",
            }
        }
    )

    product_name: str = Field(..., min_length=1, max_length=255)
    product_name_ar: str | None = Field(None, max_length=255)
    category: str | None = Field(None, max_length=100)
    image_url: str | None = Field(
        None, description="Product image URL for vision-based generation"
    )
    attributes: dict | None = Field(
        None, description="Product attributes (material, color, etc.)"
    )
    tone: str = Field(
        default="professional",
        description="Tone: professional, casual, luxury, playful",
    )


class GenerateDescriptionResponse(BaseModel):
    """Response from AI description generation."""

    short_description_en: str
    long_description_en: str
    short_description_ar: str
    long_description_ar: str
    seo_title_en: str
    seo_title_ar: str
    seo_description_en: str
    seo_description_ar: str
    tags: list[str]
