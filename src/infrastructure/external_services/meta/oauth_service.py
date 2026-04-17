"""Meta OAuth service for Facebook Login for Business."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)


class MetaOAuthService:
    """Service for Meta OAuth flow (Facebook Login for Business)."""

    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        login_config_id: str | None = None,
        redirect_uri: str | None = None,
    ):
        self.app_id = app_id or settings.meta_app_id
        self.app_secret = app_secret or settings.meta_app_secret
        self.login_config_id = login_config_id or settings.meta_login_config_id
        self.redirect_uri = redirect_uri
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    def build_authorization_url(
        self,
        state: str,
        redirect_uri: str | None = None,
    ) -> str:
        """Build the Facebook Login for Business authorization URL."""
        redirect = redirect_uri or self.redirect_uri
        if not redirect:
            raise ValueError("Redirect URI not configured")

        base_url = "https://www.facebook.com/v21.0/dialog/oauth"
        params = {
            "client_id": self.app_id,
            "redirect_uri": redirect,
            "state": state,
            "response_type": "code",
            "scope": ",".join([
                "pages_messaging",
                "pages_show_list",
                "instagram_basic",
                "instagram_manage_messages",
                "instagram_manage_insights",
                "whatsapp_business_messaging",
                "whatsapp_business_management",
                "catalog_management",
                "business_management",
            ]),
            "config_id": self.login_config_id,
        }

        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base_url}?{query_string}"

    def generate_state(self) -> str:
        """Generate a secure state parameter for OAuth."""
        return secrets.token_urlsafe(32)

    async def exchange_code_for_tokens(
        self,
        code: str,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        """Exchange authorization code for access token."""
        redirect = redirect_uri or self.redirect_uri
        if not redirect:
            raise ValueError("Redirect URI not configured")

        url = "https://graph.facebook.com/v21.0/oauth/access_token"
        params = {
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "redirect_uri": redirect,
            "code": code,
        }

        logger.info("meta_oauth_token_exchange", redirect_uri=redirect)

        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 0)

        if not access_token:
            raise ValueError("No access token in response")

        return {
            "access_token": access_token,
            "expires_at": datetime.now(UTC) + timedelta(seconds=expires_in)
            if expires_in
            else None,
            "token_type": data.get("token_type", "bearer"),
        }

    async def exchange_short_lived_for_long_lived(
        self,
        short_lived_token: str,
    ) -> dict[str, Any]:
        """Exchange short-lived token for long-lived token (60 days)."""
        url = "https://graph.facebook.com/v21.0/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "fb_exchange_token": short_lived_token,
        }

        logger.info("meta_oauth_long_lived_exchange")

        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 0)

        return {
            "access_token": access_token,
            "expires_at": datetime.now(UTC) + timedelta(seconds=expires_in)
            if expires_in
            else None,
            "token_type": data.get("token_type", "bearer"),
        }

    async def get_pages(self, access_token: str) -> list[dict[str, Any]]:
        """Get Facebook Pages for the user."""
        url = "https://graph.facebook.com/v21.0/me/accounts"
        params = {
            "access_token": access_token,
            "fields": "id,name,access_token,tasks,perms",
        }

        logger.debug("meta_get_pages")

        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        result: list[dict[str, Any]] = data.get("data", [])
        return result

    async def get_instagram_business_account(
        self,
        page_id: str,
        page_access_token: str,
    ) -> dict[str, Any] | None:
        """Get Instagram Business account linked to a Facebook Page."""
        url = f"https://graph.facebook.com/v21.0/{page_id}"
        params = {
            "access_token": page_access_token,
            "fields": "instagram_business_account",
        }

        logger.debug("meta_get_instagram_account", page_id=page_id)

        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        ig_account = data.get("instagram_business_account")
        if ig_account:
            ig_id = ig_account.get("id")
            return await self.get_instagram_profile(ig_id, page_access_token)
        return None

    async def get_instagram_profile(
        self,
        ig_account_id: str,
        access_token: str,
    ) -> dict[str, Any]:
        """Get Instagram Business account profile."""
        url = f"https://graph.facebook.com/v21.0/{ig_account_id}"
        params = {
            "access_token": access_token,
            "fields": "id,username,name,profile_picture_url,biography",
        }

        logger.debug("meta_get_instagram_profile", ig_id=ig_account_id)

        response = await self._client.get(url, params=params)
        response.raise_for_status()
        result_dict: dict[str, Any] = response.json()
        return result_dict

    async def get_whatsapp_business_accounts(
        self,
        access_token: str,
    ) -> list[dict[str, Any]]:
        """Get WhatsApp Business Accounts."""
        url = "https://graph.facebook.com/v21.0/me/businesses"
        params = {
            "access_token": access_token,
            "fields": "id,name,whatsapp_business_accounts{id,phone_code_hash,verified_name,quality_score}",
        }

        logger.debug("meta_get_whatsapp_accounts")

        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        accounts = []
        for business in data.get("data", []):
            waba_list = business.get("whatsapp_business_accounts", {}).get("data", [])
            for waba in waba_list:
                waba["business_id"] = business.get("id")
                waba["business_name"] = business.get("name")
                accounts.append(waba)
        return accounts

    async def get_whatsapp_phone_numbers(
        self,
        waba_id: str,
        access_token: str,
    ) -> list[dict[str, Any]]:
        """Get phone numbers for a WhatsApp Business Account."""
        url = f"https://graph.facebook.com/v21.0/{waba_id}/phone_numbers"
        params = {
            "access_token": access_token,
            "fields": "id,display_name,verified,code_verification_status,quality_rating",
        }

        logger.debug("meta_get_whatsapp_phones", waba_id=waba_id)

        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        result: list[dict[str, Any]] = data.get("data", [])
        return result

    async def subscribe_page_to_webhook(
        self,
        page_id: str,
        page_access_token: str,
        callback_url: str,
        verify_token: str,
    ) -> bool:
        """Subscribe a Facebook Page to webhook callbacks."""
        url = f"https://graph.facebook.com/v21.0/{page_id}/subscriptions"
        data = {
            "object": "page",
            "callback_url": callback_url,
            "verify_token": verify_token,
            "fields": "messages,messaging_postbacks,messaging_handovers,message_deliveries,message_reads",
            "access_token": page_access_token,
        }

        logger.info(
            "meta_subscribe_page_webhook", page_id=page_id, callback_url=callback_url
        )

        response = await self._client.post(url, json=data)
        if response.status_code == 200:
            return True
        error = response.json()
        logger.warning("meta_subscribe_failed", page_id=page_id, error=error)
        return False

    async def subscribe_waba_to_webhook(
        self,
        waba_id: str,
        access_token: str,
        callback_url: str,
        verify_token: str,
    ) -> bool:
        """Subscribe a WhatsApp Business Account to webhook callbacks."""
        url = f"https://graph.facebook.com/v21.0/{waba_id}/subscribed_apps"
        data = {
            "access_token": access_token,
        }

        logger.info(
            "meta_subscribe_waba_webhook", waba_id=waba_id, callback_url=callback_url
        )

        response = await self._client.post(url, json=data)
        if response.status_code == 200:
            return True
        error = response.json()
        logger.warning("meta_subscribe_waba_failed", waba_id=waba_id, error=error)
        return False
