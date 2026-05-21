"""Meta Graph API social service.

Handles OAuth token exchange, account info retrieval, and post fetching
for both Instagram and Facebook via the Meta Graph API.

Docs: https://developers.facebook.com/docs/graph-api
      https://developers.facebook.com/docs/instagram-api

Falls back to mock data when META_APP_ID / META_APP_SECRET are not configured.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from src.config import settings
from src.core.entities.social_connection import SocialPlatform
from src.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com"


@dataclass
class SocialAccountInfo:
    """Account info returned after OAuth connection."""

    platform_account_id: str
    handle: str
    followers: int
    posts_count: int


@dataclass
class FetchedPost:
    """A single post fetched from the social platform."""

    platform_post_id: str
    image_url: str
    caption: str
    likes: int
    comments: int
    posted_at: datetime
    suggested_name: str | None = None
    suggested_name_ar: str | None = None
    suggested_price: int | None = None


def _extract_price(text: str) -> int | None:
    """Try to extract a price in EGP from caption text."""
    # Match patterns like "349 EGP", "EGP 349", "349 LE", "349جنيه", "٣٤٩"
    patterns = [
        r"(\d[\d,]*)\s*(?:EGP|egp|LE|le|جنيه|ج\.م)",
        r"(?:EGP|egp|LE|le)\s*(\d[\d,]*)",
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            try:
                return int(match.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _extract_name(caption: str) -> str | None:
    """Extract a likely product name from the first line of a caption."""
    first_line = caption.split("\n")[0].strip()
    # Strip emojis
    cleaned = re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        r"\U0001F900-\U0001F9FF\U00002702-\U000027B0]+",
        "",
        first_line,
    )
    cleaned = cleaned.strip(" -—|•!.,")
    # Remove common Instagram filler
    for filler in ["DM to order", "Link in bio", "Available now", "NEW", "🔥"]:
        cleaned = cleaned.replace(filler, "")
    cleaned = cleaned.strip()
    if len(cleaned) > 3:
        return cleaned[:100]
    return None


class MetaSocialService:
    """Meta Graph API service for social import.

    When META_APP_ID and META_APP_SECRET are configured, makes real API calls.
    Otherwise falls back to mock data for development.
    """

    def __init__(self) -> None:
        self.app_id = settings.meta_app_id
        self.app_secret = settings.meta_app_secret
        self.api_version = settings.meta_graph_api_version
        self._is_configured = bool(self.app_id and self.app_secret)

        if not self._is_configured:
            logger.warning(
                "META_APP_ID / META_APP_SECRET not set — using mock social data"
            )

    @property
    def _graph_url(self) -> str:
        return f"{GRAPH_BASE}/{self.api_version}"

    # ------------------------------------------------------------------
    # Step 1: OAuth
    # ------------------------------------------------------------------

    def _ensure_platform(self, platform: SocialPlatform | str) -> SocialPlatform:
        """Coerce a string to SocialPlatform enum if needed."""
        if isinstance(platform, str):
            return SocialPlatform(platform)
        return platform

    def get_auth_url(self, platform: SocialPlatform | str, redirect_uri: str) -> str:
        """Build the OAuth authorization URL.

        For Instagram, we use Facebook Login with instagram_basic + instagram_content_publish
        scopes because the Instagram Graph API is accessed through Facebook OAuth.
        """
        platform = self._ensure_platform(platform)
        if not self._is_configured:
            return self._mock_auth_url(platform, redirect_uri)

        # Both Instagram and Facebook use Facebook Login OAuth
        scopes = {
            SocialPlatform.INSTAGRAM: (
                "instagram_basic,instagram_manage_insights,"
                "pages_show_list,pages_read_engagement"
            ),
            SocialPlatform.FACEBOOK: (
                "pages_show_list,pages_read_engagement,pages_read_user_content"
            ),
        }

        params = {
            "client_id": self.app_id,
            "redirect_uri": redirect_uri,
            "scope": scopes[platform],
            "response_type": "code",
            "state": f"{platform.value}_{uuid4().hex[:12]}",
        }
        return f"https://www.facebook.com/{self.api_version}/dialog/oauth?{urlencode(params)}"

    # ------------------------------------------------------------------
    # Step 2: Token exchange
    # ------------------------------------------------------------------

    async def exchange_token(self, platform: SocialPlatform | str, code: str) -> str:
        """Exchange a short-lived OAuth code for a long-lived access token.

        Flow:
        1. Exchange code → short-lived token (valid ~1 hour)
        2. Exchange short-lived → long-lived token (valid ~60 days)
        """
        platform = self._ensure_platform(platform)
        if not self._is_configured:
            return self._mock_token(platform)

        async with httpx.AsyncClient(timeout=15) as client:
            # Step 2a: code → short-lived token
            resp = await client.get(
                f"{self._graph_url}/oauth/access_token",
                params={
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    "code": code,
                    "redirect_uri": "https://merchant.numueg.app/social/callback",
                },
            )
            if resp.status_code != 200:
                logger.error("Token exchange failed: %s", resp.text)
                raise ExternalServiceError(
                    "Meta", f"OAuth token exchange failed: {resp.status_code}"
                )
            short_token: str = resp.json()["access_token"]

            # Step 2b: short-lived → long-lived token
            resp = await client.get(
                f"{self._graph_url}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self.app_id,
                    "client_secret": self.app_secret,
                    "fb_exchange_token": short_token,
                },
            )
            if resp.status_code != 200:
                logger.warning("Long-lived token exchange failed, using short-lived")
                return short_token

            token: str = resp.json()["access_token"]
            return token

    # ------------------------------------------------------------------
    # Step 3: Account info
    # ------------------------------------------------------------------

    async def get_account_info(
        self, platform: SocialPlatform | str, access_token: str
    ) -> SocialAccountInfo:
        """Fetch the connected account's profile info."""
        platform = self._ensure_platform(platform)
        if not self._is_configured:
            return self._mock_account_info(platform)

        async with httpx.AsyncClient(timeout=15) as client:
            if platform == SocialPlatform.INSTAGRAM:
                return await self._get_instagram_account(client, access_token)
            else:
                return await self._get_facebook_page(client, access_token)

    async def _get_instagram_account(
        self, client: httpx.AsyncClient, access_token: str
    ) -> SocialAccountInfo:
        """Get Instagram Business/Creator account via linked Facebook Page.

        The Instagram Graph API requires:
        1. Get user's Facebook Pages
        2. For each Page, get the linked Instagram account
        """
        # Get user's pages
        resp = await client.get(
            f"{self._graph_url}/me/accounts",
            params={
                "access_token": access_token,
                "fields": "id,name,instagram_business_account",
            },
        )
        resp.raise_for_status()
        pages = resp.json().get("data", [])

        # Find the first page with an Instagram account linked
        ig_account_id = None
        for page in pages:
            ig = page.get("instagram_business_account")
            if ig:
                ig_account_id = ig["id"]
                break

        if not ig_account_id:
            raise ExternalServiceError(
                "Meta",
                "No Instagram Business account linked to any of your Facebook Pages. "
                "Please convert your Instagram to a Business or Creator account and link it to a Facebook Page.",
            )

        # Get Instagram account details
        resp = await client.get(
            f"{self._graph_url}/{ig_account_id}",
            params={
                "access_token": access_token,
                "fields": "id,username,followers_count,media_count",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        return SocialAccountInfo(
            platform_account_id=data["id"],
            handle=f"@{data.get('username', 'unknown')}",
            followers=data.get("followers_count", 0),
            posts_count=data.get("media_count", 0),
        )

    async def _get_facebook_page(
        self, client: httpx.AsyncClient, access_token: str
    ) -> SocialAccountInfo:
        """Get the merchant's primary Facebook Page info."""
        resp = await client.get(
            f"{self._graph_url}/me/accounts",
            params={
                "access_token": access_token,
                "fields": "id,name,followers_count,fan_count",
            },
        )
        resp.raise_for_status()
        pages = resp.json().get("data", [])

        if not pages:
            raise ExternalServiceError(
                "Meta", "No Facebook Pages found for this account."
            )

        page = pages[0]  # Use the first page
        return SocialAccountInfo(
            platform_account_id=page["id"],
            handle=page.get("name", "Unknown Page"),
            followers=page.get("followers_count") or page.get("fan_count", 0),
            posts_count=0,  # Page post count requires separate call
        )

    # ------------------------------------------------------------------
    # Step 4: Fetch posts
    # ------------------------------------------------------------------

    async def fetch_posts(
        self,
        platform: SocialPlatform | str,
        access_token: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[FetchedPost], str | None]:
        """Fetch recent posts from the connected account.

        Returns (posts, next_cursor).
        """
        platform = self._ensure_platform(platform)
        if not self._is_configured:
            return self._mock_posts(platform, limit)

        async with httpx.AsyncClient(timeout=30) as client:
            if platform == SocialPlatform.INSTAGRAM:
                return await self._fetch_instagram_posts(
                    client, access_token, limit, cursor
                )
            else:
                return await self._fetch_facebook_posts(
                    client, access_token, limit, cursor
                )

    async def _fetch_instagram_posts(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[FetchedPost], str | None]:
        """Fetch Instagram media via the Instagram Graph API.

        Endpoint: GET /{ig-user-id}/media?fields=id,caption,media_url,like_count,comments_count,timestamp
        """
        # First get the IG account ID
        resp = await client.get(
            f"{self._graph_url}/me/accounts",
            params={
                "access_token": access_token,
                "fields": "instagram_business_account",
            },
        )
        resp.raise_for_status()
        pages = resp.json().get("data", [])

        ig_account_id = None
        for page in pages:
            ig = page.get("instagram_business_account")
            if ig:
                ig_account_id = ig["id"]
                break

        if not ig_account_id:
            return [], None

        # Fetch media
        params = {
            "access_token": access_token,
            "fields": "id,caption,media_url,thumbnail_url,media_type,like_count,comments_count,timestamp",
            "limit": str(min(limit, 100)),  # API max is 100 per page
        }
        if cursor:
            params["after"] = cursor

        resp = await client.get(
            f"{self._graph_url}/{ig_account_id}/media",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        posts: list[FetchedPost] = []
        for item in data.get("data", []):
            # Skip videos/reels — only import images and carousels
            media_type = item.get("media_type", "IMAGE")
            if media_type == "VIDEO":
                image_url = item.get("thumbnail_url", "")
            else:
                image_url = item.get("media_url", "")

            if not image_url:
                continue

            caption = item.get("caption", "")
            posts.append(
                FetchedPost(
                    platform_post_id=item["id"],
                    image_url=image_url,
                    caption=caption,
                    likes=item.get("like_count", 0),
                    comments=item.get("comments_count", 0),
                    posted_at=datetime.fromisoformat(
                        item["timestamp"].replace("Z", "+00:00")
                    ),
                    suggested_name=_extract_name(caption) if caption else None,
                    suggested_price=_extract_price(caption) if caption else None,
                )
            )

        # Pagination cursor
        next_cursor = None
        paging = data.get("paging", {})
        if "next" in paging:
            cursors = paging.get("cursors", {})
            next_cursor = cursors.get("after")

        return posts, next_cursor

    async def _fetch_facebook_posts(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[FetchedPost], str | None]:
        """Fetch Facebook Page posts with photos.

        Endpoint: GET /{page-id}/posts?fields=id,message,full_picture,created_time,likes.summary(true),comments.summary(true)
        """
        # Get page ID and page access token
        resp = await client.get(
            f"{self._graph_url}/me/accounts",
            params={"access_token": access_token, "fields": "id,access_token"},
        )
        resp.raise_for_status()
        pages = resp.json().get("data", [])

        if not pages:
            return [], None

        page = pages[0]
        page_id = page["id"]
        page_token = page["access_token"]

        # Fetch posts
        params = {
            "access_token": page_token,
            "fields": "id,message,full_picture,created_time,likes.summary(true),comments.summary(true)",
            "limit": min(limit, 100),
        }
        if cursor:
            params["after"] = cursor

        resp = await client.get(
            f"{self._graph_url}/{page_id}/posts",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        posts: list[FetchedPost] = []
        for item in data.get("data", []):
            image_url = item.get("full_picture")
            if not image_url:
                continue  # Skip posts without images

            caption = item.get("message", "")
            likes_data = item.get("likes", {}).get("summary", {})
            comments_data = item.get("comments", {}).get("summary", {})

            posts.append(
                FetchedPost(
                    platform_post_id=item["id"],
                    image_url=image_url,
                    caption=caption,
                    likes=likes_data.get("total_count", 0),
                    comments=comments_data.get("total_count", 0),
                    posted_at=datetime.fromisoformat(
                        item["created_time"].replace("+0000", "+00:00")
                    ),
                    suggested_name=_extract_name(caption) if caption else None,
                    suggested_price=_extract_price(caption) if caption else None,
                )
            )

        # Pagination
        next_cursor = None
        paging = data.get("paging", {})
        if "next" in paging:
            cursors = paging.get("cursors", {})
            next_cursor = cursors.get("after")

        return posts, next_cursor

    # ------------------------------------------------------------------
    # Mock fallbacks (used when META_APP_ID not configured)
    # ------------------------------------------------------------------

    def _mock_auth_url(self, platform: SocialPlatform, redirect_uri: str) -> str:
        return (
            f"https://api.{platform.value}.com/oauth/authorize"
            f"?client_id=mock_numu_app"
            f"&redirect_uri={redirect_uri}"
            f"&scope=user_profile,user_media"
            f"&state={uuid4().hex}"
        )

    def _mock_token(self, platform: SocialPlatform) -> str:
        logger.info("Mock: exchanging OAuth code for %s", platform.value)
        return f"mock_token_{platform.value}_{uuid4().hex[:8]}"

    def _mock_account_info(self, platform: SocialPlatform) -> SocialAccountInfo:
        mock_data = {
            SocialPlatform.INSTAGRAM: SocialAccountInfo(
                platform_account_id="ig_17841400000000",
                handle="@numu.egypt",
                followers=12400,
                posts_count=156,
            ),
            SocialPlatform.FACEBOOK: SocialAccountInfo(
                platform_account_id="fb_100000000000000",
                handle="NUMU Egypt",
                followers=8500,
                posts_count=89,
            ),
        }
        return mock_data[platform]

    def _mock_posts(
        self, platform: SocialPlatform, limit: int
    ) -> tuple[list[FetchedPost], str | None]:
        logger.info("Mock: fetching %d posts from %s", limit, platform.value)
        now = datetime.now(UTC)
        mock_posts = [
            FetchedPost(
                platform_post_id=f"{platform.value}_post_{i}_{uuid4().hex[:6]}",
                image_url=f"https://picsum.photos/seed/{uuid4().hex[:6]}/800/800",
                caption=caption,
                likes=likes,
                comments=comments,
                posted_at=now - timedelta(days=i * 3),
                suggested_name=name_en,
                suggested_name_ar=name_ar,
                suggested_price=price,
            )
            for i, (caption, likes, comments, name_en, name_ar, price) in enumerate([
                (
                    "New drop! Premium Egyptian cotton tee DM to order",
                    342,
                    28,
                    "Egyptian Cotton Tee",
                    "تيشيرت قطن مصري",
                    349,
                ),
                (
                    "Handmade leather wallet — genuine cow leather",
                    189,
                    15,
                    "Leather Wallet",
                    "محفظة جلد طبيعي",
                    450,
                ),
                (
                    "Limited edition hoodie, only 50 pieces made",
                    567,
                    42,
                    "Limited Edition Hoodie",
                    "هودي إصدار محدود",
                    699,
                ),
                (
                    "Linen summer dress — perfect for the beach",
                    423,
                    31,
                    "Linen Summer Dress",
                    "فستان كتان صيفي",
                    550,
                ),
                (
                    "Custom embroidered cap — your name, your style",
                    278,
                    19,
                    "Custom Embroidered Cap",
                    "كاب مطرز حسب الطلب",
                    199,
                ),
                (
                    "Silver rings handcrafted in Khan El-Khalili",
                    612,
                    55,
                    "Handcrafted Silver Ring",
                    "خاتم فضة يدوي الصنع",
                    320,
                ),
                (
                    "Organic cotton baby onesie — softest thing ever",
                    234,
                    17,
                    "Organic Cotton Baby Onesie",
                    "بدلة أطفال قطن عضوي",
                    280,
                ),
                (
                    "Ceramic coffee mug — hand-painted Nubian design",
                    156,
                    12,
                    "Hand-painted Ceramic Mug",
                    "كوب سيراميك مرسوم يدوياً",
                    175,
                ),
            ])
        ]
        return mock_posts[:limit], None
