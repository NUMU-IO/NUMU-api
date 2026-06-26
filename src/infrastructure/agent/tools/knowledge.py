"""`search_knowledge` read tool (US4) — two-layer NUMU RAG with citations.

Answers "what is / how do I" questions about NUMU by retrieving from the shared
platform corpus (Layer A) + ONLY the caller tenant's Layer B. Returns chunks with
their source doc so the agent can cite; if nothing matches, the agent says it
isn't documented rather than guessing (Constitution II/VIII).
"""

from __future__ import annotations

from typing import Any

from src.application.agent.tools import ToolContext, ToolResult
from src.config import settings as app_settings
from src.config.logging_config import get_logger
from src.core.agent.entities import RiskTier
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository

logger = get_logger(__name__)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The NUMU how-to / what-is question.",
        },
        "top_k": {"type": "integer", "minimum": 1, "maximum": 8},
    },
    "required": ["query"],
    "additionalProperties": False,
}


async def search_knowledge(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = (args.get("query") or "").strip()
    if not query:
        return ToolResult.invalid_args("A 'query' is required.")
    k = int(args.get("top_k") or app_settings.agent_knowledge_top_k)
    k = max(1, min(k, 8))

    try:
        embedding = await get_embedder().embed_query(query)
        chunks = await KnowledgeRepository(ctx.session).search(
            embedding, tenant_id=ctx.tenant_id, k=k
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_tool_error", tool="search_knowledge", error=str(exc))
        return ToolResult.unavailable(
            "Could not search the NUMU knowledge base right now."
        )

    if not chunks:
        # Grounding: no source → the agent must say it isn't documented.
        return ToolResult(ok=True, data={"chunks": []})

    return ToolResult(
        ok=True,
        data={
            "chunks": [
                {
                    "content": c["content"],
                    "score": round(c["score"], 4),
                    "doc": c["doc"],
                }
                for c in chunks
            ]
        },
        source=[
            {
                "type": "doc",
                "id": c["doc"]["id"],
                "title": c["doc"]["title"],
                "source": c["doc"].get("source", ""),
            }
            for c in chunks
        ],
    )


SPEC = {
    "name": "search_knowledge",
    "description": (
        "Search NUMU's documentation to answer 'what is' / 'how do I' questions about the "
        "platform and its features (e.g. setting up Paymob, BOGO campaigns, the theme editor). "
        "Cite the returned source. If it returns nothing, say it isn't documented — don't guess."
    ),
    "input_schema": INPUT_SCHEMA,
    "risk_tier": RiskTier.AUTO,
    "required_permission": None,  # Layer B is tenant-filtered; Layer A holds no merchant data.
    "executor": search_knowledge,
}
