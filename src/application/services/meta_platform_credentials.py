"""Resolve and securely persist platform-wide Meta credentials."""

import base64
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)
from src.infrastructure.external_services.secrets.secrets_manager import (
    get_secrets_manager,
)

META_CONFIG_KEY = "meta_credentials"


@dataclass(frozen=True)
class MetaPlatformCredentials:
    app_id: str = ""
    app_secret: str = ""
    webhook_verify_token: str = ""
    phone_registration_pin: str = ""
    login_config_id: str = ""
    embedded_signup_config_id: str = ""
    graph_api_version: str = "v25.0"


async def get_meta_platform_credentials(db: AsyncSession) -> MetaPlatformCredentials:
    """Return database overrides, falling back to environment settings."""
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == META_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    value = row.value if row and isinstance(row.value, dict) else {}
    secrets: dict[str, str] = {}
    ciphertext = value.get("secrets_ciphertext_b64")
    key_id = value.get("secrets_key_id")
    if ciphertext and key_id:
        manager = get_secrets_manager()
        secrets = await manager.decrypt(base64.b64decode(ciphertext), key_id)

    # Read legacy plaintext once so existing installations keep working. New
    # writes remove these keys and persist only encrypted secret material.
    app_secret = secrets.get("meta_app_secret") or value.get("meta_app_secret")
    verify_token = secrets.get("meta_webhook_verify_token") or value.get(
        "meta_webhook_verify_token"
    )
    registration_pin = secrets.get("meta_phone_registration_pin") or value.get(
        "meta_phone_registration_pin"
    )
    return MetaPlatformCredentials(
        app_id=value.get("meta_app_id") or settings.meta_app_id or "",
        app_secret=app_secret or settings.meta_app_secret or "",
        webhook_verify_token=verify_token
        or settings.meta_webhook_verify_token
        or settings.whatsapp_webhook_verify_token
        or "",
        phone_registration_pin=registration_pin
        or settings.meta_phone_registration_pin
        or "",
        login_config_id=value.get("meta_login_config_id")
        or settings.meta_login_config_id
        or "",
        embedded_signup_config_id=value.get("meta_config_id")
        or settings.meta_config_id
        or "",
        graph_api_version=value.get("meta_graph_api_version")
        or settings.meta_graph_api_version,
    )


async def encrypt_meta_secrets(
    app_secret: str, webhook_token: str, phone_registration_pin: str
) -> dict[str, str]:
    manager = get_secrets_manager()
    key_id = await manager.get_current_key_id()
    encrypted = await manager.encrypt(
        {
            "meta_app_secret": app_secret,
            "meta_webhook_verify_token": webhook_token,
            "meta_phone_registration_pin": phone_registration_pin,
        },
        key_id,
    )
    return {
        "secrets_ciphertext_b64": base64.b64encode(encrypted).decode("ascii"),
        "secrets_key_id": key_id,
    }
