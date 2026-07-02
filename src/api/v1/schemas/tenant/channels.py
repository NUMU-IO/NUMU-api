"""Pydantic schemas for sales-channel connection endpoints (TikTok Shop)."""

from datetime import datetime

from pydantic import BaseModel, Field


class ConnectTikTokShopRequest(BaseModel):
    """Body for ``PUT /stores/{id}/settings/channels/tiktok-shop``.

    Submitted by the hub after the OAuth callback returns the token bundle +
    chosen shop. The tokens are encrypted into a ServiceCredential; the shop
    metadata goes into ``store.settings.channels.tiktok_shop``.
    """

    access_token: str = Field(..., min_length=8, max_length=1024)
    refresh_token: str | None = Field(default=None, max_length=1024)
    shop_id: str = Field(..., min_length=1, max_length=64)
    shop_cipher: str = Field(..., min_length=1, max_length=256)
    shop_name: str | None = Field(default=None, max_length=128)
    region: str | None = Field(default=None, max_length=16)
    seller_name: str | None = Field(default=None, max_length=128)


class TikTokShopStatusResponse(BaseModel):
    """Connection status for the hub's TikTok Shop card."""

    connected: bool
    shop_id: str | None = None
    shop_name: str | None = None
    region: str | None = None
    seller_name: str | None = None
    connected_at: datetime | None = None
