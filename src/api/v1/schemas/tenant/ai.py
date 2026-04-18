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
                "image_url": "https://r2.numueg.app/stores/abc/products/img.jpg",
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


class GeneratePolicyRequest(BaseModel):
    """Request to generate a store policy using AI."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "policy_type": "return",
                "store_name": "My Store",
                "answers": {
                    "return_window": "14 days",
                    "refund_method": "Original payment method",
                    "conditions": "Items must be unused and in original packaging",
                },
                "language": "en",
            }
        }
    )

    policy_type: str = Field(
        ..., description="Policy type: return, shipping, privacy, terms"
    )
    store_name: str = Field(..., min_length=1, max_length=255)
    answers: dict[str, str] = Field(
        ..., description="Answers to policy-specific questions"
    )
    language: str = Field(default="en", description="Language: en or ar")


class GeneratePolicyResponse(BaseModel):
    """Response from AI policy generation."""

    policy_text: str
