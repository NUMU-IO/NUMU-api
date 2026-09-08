"""US3 — derive a tenant's catalog + policies into Layer B (per-tenant, RLS).

Builds slow-moving, retrievable knowledge from the store's own data — product
titles/descriptions and store policies — so the Agent can answer "do I sell X?" and
"what's my return policy?" from the merchant's own content. Fast-changing facts
(stock levels, today's orders) stay on live tools, NOT embeddings.

Idempotent (source-keyed) and tenant-scoped: every doc carries `tenant_id` and is
written under the caller's tenant context (RLS). Heavy/bulk re-indexing runs off the
request path (Celery / the `tenant_layerb_reindex` n8n lane).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.agent.knowledge.embedder import get_embedder
from src.infrastructure.agent.knowledge.repository import KnowledgeRepository

logger = get_logger(__name__)

# Products per embedding request. Small enough to stay well inside provider
# payload limits, large enough that a 1,000-product catalogue is ~50 calls
# rather than 1,000.
_EMBED_BATCH = 20


async def _fetch_rows(session: AsyncSession, sql: str, params: dict) -> list:
    """Read one source, tolerating its absence.

    The swallow is deliberate — a missing optional source should not stop the
    rest of the index — but it cannot be done on the shared session. Postgres
    aborts the whole transaction on a failed statement, so catching the
    exception here and carrying on meant every later write, and every earlier
    one, was discarded at commit while the caller was told it had indexed
    everything. A savepoint keeps the failure local to this query.
    """
    try:
        async with session.begin_nested():
            result = await session.execute(text(sql), params)
            return list(result.all())
    except Exception as exc:  # noqa: BLE001 — absent table/column → skip that source
        logger.warning("tenant_index_query_failed", error=str(exc))
        return []


async def reindex_catalog(
    session: AsyncSession, *, tenant_id: UUID, store_id: UUID
) -> int:
    """Index product titles/descriptions as Layer-B 'catalog' docs. Returns docs."""
    rows = await _fetch_rows(
        session,
        "SELECT id, name, COALESCE(description, '') FROM public.products "
        "WHERE store_id = :sid LIMIT 1000",
        {"sid": str(store_id)},
    )
    embedder = get_embedder()
    repo = KnowledgeRepository(session)
    count = 0

    # One embedding request per product is one HTTP call per product. A store
    # with a real catalogue rate-limits the provider long before it finishes —
    # vionne's first run died on 429 partway through. The endpoint takes a
    # list, so ask for a batch at a time.
    bodies = [f"{name}\n\n{description}".strip() for _, name, description in rows]
    vectors: list[list[float]] = []
    for start in range(0, len(bodies), _EMBED_BATCH):
        vectors.extend(
            await embedder.embed_passages(bodies[start : start + _EMBED_BATCH])
        )

    for (pid, name, _description), body, vector in zip(
        rows, bodies, vectors, strict=True
    ):
        embeddings = [vector]
        await repo.upsert_tenant_doc(
            tenant_id=tenant_id,
            source=f"tenant-catalog/{pid}",
            title=name,
            section="Catalog",
            locale="en",
            chunks=[body],
            embeddings=embeddings,
            source_kind="catalog",
            status="published",
        )
        count += 1
    return count


async def reindex_policies(
    session: AsyncSession, *, tenant_id: UUID, store_id: UUID
) -> int:
    """Index store policies (return/shipping/etc.) as Layer-B 'policy' docs."""
    # Policies live in `stores.settings->'policies'`, the same JSONB the
    # storefront reads. There is no `store_settings` table and there never
    # was — this queried one, which is why policy indexing had never once
    # returned a row.
    raw = await _fetch_rows(
        session,
        "SELECT settings -> 'policies' FROM public.stores WHERE id = :sid",
        {"sid": str(store_id)},
    )
    policies = (raw[0][0] if raw else None) or {}
    if not isinstance(policies, dict):
        return 0
    rows = [(k, v) for k, v in policies.items() if isinstance(v, str) and v.strip()]
    embedder = get_embedder()
    repo = KnowledgeRepository(session)
    count = 0
    for key, value in rows:
        if not value:
            continue
        body = f"{key}: {value}"
        embeddings = await embedder.embed_passages([body])
        await repo.upsert_tenant_doc(
            tenant_id=tenant_id,
            source=f"tenant-policy/{key}",
            title=str(key),
            section="Policies",
            locale="en",
            chunks=[body],
            embeddings=embeddings,
            source_kind="policy",
            status="published",
        )
        count += 1
    return count


async def reindex_tenant(
    session: AsyncSession, *, tenant_id: UUID, store_id: UUID
) -> dict:
    catalog = await reindex_catalog(session, tenant_id=tenant_id, store_id=store_id)
    policies = await reindex_policies(session, tenant_id=tenant_id, store_id=store_id)
    logger.info(
        "tenant_reindexed", tenant_id=str(tenant_id), catalog=catalog, policies=policies
    )
    return {"catalog_docs": catalog, "policy_docs": policies}


async def on_store_change(
    tenant_id: UUID, store_id: UUID, *, scope: str = "catalog"
) -> None:
    """Store-change hook (FR-005): re-embed a tenant's Layer B near-real-time.

    Call this from catalog/policy mutation paths (e.g. after a product or policy is
    created/updated). It offloads the re-index to the allow-listed `tenant_layerb_reindex`
    n8n workflow, which calls back into a tenant-scoped, secret-guarded reindex endpoint —
    keeping the heavy work off the request path. Best-effort: a dispatch failure is logged,
    never raised, so it can't break the originating store mutation.
    """
    from src.infrastructure.agent.n8n.client import get_n8n_client

    workflow = "tenant_layerb_reindex"
    client = get_n8n_client()
    if not client.is_allowed(workflow):
        return
    try:
        await client.trigger(
            workflow,
            tenant_id=tenant_id,
            params={"store_id": str(store_id), "scope": scope},
        )
    except Exception as exc:  # noqa: BLE001 — never break the store mutation
        logger.warning("tenant_layerb_reindex_dispatch_failed", error=str(exc))
