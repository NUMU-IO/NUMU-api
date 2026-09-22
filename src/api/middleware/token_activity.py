"""Per-token request trail for personal access and Partner App tokens.

The API otherwise keeps only ``last_used_at`` per token, so "what is this
token doing?" meant stitching nginx and MCP logs together by timestamp. Every
request that presents a known token now leaves one entry here, including the
ones the auth dependency refuses (wrong store, missing scope, API access off)
— those refusals are exactly what an admin watching a token wants to see.

Entries live in one capped Redis list per token, newest first.
ponytail: last 1000 requests per token, 30-day idle expiry, lost if Redis is
flushed. Move to a Postgres table when we need longer history or queries
across tokens.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import Request

from src.api.middleware.rate_limit import _get_cache, _get_client_ip
from src.core.logging import get_logger

logger = get_logger(__name__)

KEEP = 1000
IDLE_TTL_SECONDS = 30 * 24 * 3600


def _key(token_id: str) -> str:
    return f"api_token_log:{token_id}"


async def record_token_request(
    request: Request, status_code: int, duration_ms: float
) -> None:
    """Append this request to its token's trail. Never raises."""
    pat = getattr(request.state, "pat", None)
    if not pat:
        return
    query = request.url.query
    entry = {
        "at": datetime.now(UTC).isoformat(),
        "method": request.method,
        "path": request.url.path + (f"?{query[:300]}" if query else ""),
        "status": status_code,
        "ip": _get_client_ip(request),
        "user_agent": request.headers.get("user-agent", "")[:160],
        "ms": duration_ms,
    }
    try:
        client = await _get_cache()._get_client()
        key = _key(pat["token_id"])
        async with client.pipeline(transaction=False) as pipe:
            pipe.lpush(key, json.dumps(entry))
            pipe.ltrim(key, 0, KEEP - 1)
            pipe.expire(key, IDLE_TTL_SECONDS)
            await pipe.execute()
    except Exception:
        logger.warning("api_token_activity_record_failed", token_id=pat.get("token_id"))


async def recent_requests(token_id: str, limit: int = KEEP) -> list[dict]:
    """Newest-first entries for one token."""
    client = await _get_cache()._get_client()
    raw = await client.lrange(_key(token_id), 0, max(0, min(limit, KEEP) - 1))
    return [json.loads(r) for r in raw]
