"""Short-lived cache for a Meta OAuth token between consent and asset choice.

The merchant picks which Pages to connect AFTER consent, but the OAuth
code is single-use — so the exchanged token is parked here (encrypted,
10-minute TTL) and consumed once the selection arrives.
"""

import base64
import json

import redis.asyncio as redis

from src.config import settings
from src.core.logging import get_logger
from src.infrastructure.external_services.secrets.secrets_manager import SecretsManager

logger = get_logger(__name__)

_TTL_SECONDS = 600


class MetaOAuthTokenCache:
    """Encrypted, expiring store for in-flight OAuth tokens."""

    def __init__(
        self,
        redis_client: redis.Redis | None = None,
        secrets_manager: SecretsManager | None = None,
    ) -> None:
        self._redis = redis_client or redis.from_url(settings.redis_url)
        self._secrets = secrets_manager or SecretsManager()

    @staticmethod
    def _key(store_id: str, state: str) -> str:
        return f"meta_oauth_pending:{store_id}:{state}"

    async def put(self, store_id: str, state: str, payload: dict) -> None:
        key_id = await self._secrets.get_current_key_id()
        encrypted = await self._secrets.encrypt(payload, key_id)
        envelope = json.dumps({
            "key_id": key_id,
            "data": base64.b64encode(encrypted).decode(),
        })
        await self._redis.set(self._key(store_id, state), envelope, ex=_TTL_SECONDS)

    async def take(self, store_id: str, state: str) -> dict | None:
        """Read and delete — a selection can only be submitted once."""
        key = self._key(store_id, state)
        raw = await self._redis.get(key)
        if not raw:
            return None
        await self._redis.delete(key)
        try:
            envelope = json.loads(raw)
            decrypted = await self._secrets.decrypt(
                base64.b64decode(envelope["data"]), envelope["key_id"]
            )
        except Exception:
            logger.warning("meta_oauth_pending_unreadable")
            return None
        return decrypted if isinstance(decrypted, dict) else None
