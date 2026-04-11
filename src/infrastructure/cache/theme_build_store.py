"""Redis-backed store for theme build status tracking."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import redis.asyncio as redis

from src.config import settings

_PREFIX = "theme_build:"
_TTL = 60 * 60 * 24  # 24 hours


def _serialize(value: Any) -> str:
    def _default(obj: object) -> str:
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    return json.dumps(value, default=_default)


class ThemeBuildStore:
    """Thin Redis wrapper that stores build status dicts keyed by build_id."""

    def __init__(self) -> None:
        self._client: redis.Redis | None = None

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._client

    async def get(self, build_id: str) -> dict[str, Any] | None:
        client = await self._get_client()
        raw = await client.get(f"{_PREFIX}{build_id}")
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, build_id: str, data: dict[str, Any]) -> None:
        client = await self._get_client()
        await client.set(f"{_PREFIX}{build_id}", _serialize(data), ex=_TTL)

    async def update(self, build_id: str, updates: dict[str, Any]) -> None:
        existing = await self.get(build_id)
        if existing is None:
            return
        existing.update(updates)
        await self.set(build_id, existing)


_instance: ThemeBuildStore | None = None


def get_theme_build_store() -> ThemeBuildStore:
    global _instance
    if _instance is None:
        _instance = ThemeBuildStore()
    return _instance
