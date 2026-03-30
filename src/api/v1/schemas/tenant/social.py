"""Social import Pydantic schemas."""

from pydantic import BaseModel, ConfigDict, Field


class ConnectSocialRequest(BaseModel):
    """Request to initiate a social account connection."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "platform": "instagram",
                "oauth_code": "AQBx...",
                "redirect_uri": "https://merchant.numueg.app/social/callback",
            }
        }
    )

    platform: str = Field(..., description="Social platform: 'instagram' or 'facebook'")
    oauth_code: str | None = Field(
        None, description="OAuth authorization code (omit to get auth URL)"
    )
    redirect_uri: str = Field(
        default="https://merchant.numueg.app/social/callback",
        description="OAuth redirect URI",
    )


class SocialConnectionResponse(BaseModel):
    """Response for a social connection."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Connection UUID")
    platform: str
    handle: str
    followers: int
    posts_count: int
    status: str
    last_synced_at: str | None = None


class SocialPostResponse(BaseModel):
    """Response for a social post."""

    model_config = ConfigDict(from_attributes=True)

    platform_post_id: str
    image_url: str | None = None
    caption: str | None = None
    likes: int = 0
    comments: int = 0
    posted_at: str | None = None
    imported: bool = False
    suggested_name: str | None = None
    suggested_name_ar: str | None = None
    suggested_price: int | None = None


class SocialPostsListResponse(BaseModel):
    """Response for listing social posts."""

    posts: list[SocialPostResponse]
    next_cursor: str | None = None


class ImportPostsRequest(BaseModel):
    """Request to import social posts as products."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "post_ids": ["ig_post_1_abc123", "ig_post_2_def456"],
            }
        }
    )

    post_ids: list[str] = Field(
        ..., min_length=1, description="Platform post IDs to import"
    )


class ImportPostsResponse(BaseModel):
    """Response after importing social posts."""

    imported: int
    product_ids: list[str]
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# URL-based import (no OAuth required)
# ---------------------------------------------------------------------------


class ImportFromUrlRequest(BaseModel):
    """Request to import products from Instagram/Facebook post URLs."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "urls": [
                    "https://www.instagram.com/p/ABC123/",
                    "https://www.instagram.com/p/DEF456/",
                ],
            }
        }
    )

    urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Instagram or Facebook post URLs (max 20 per request)",
    )


class UrlImportResultResponse(BaseModel):
    """Result for a single URL import."""

    url: str
    product_id: str | None = None
    product_name: str | None = None
    images_count: int = 0
    error: str | None = None


class ImportFromUrlResponse(BaseModel):
    """Response after importing from URLs."""

    imported: int
    results: list[UrlImportResultResponse]
