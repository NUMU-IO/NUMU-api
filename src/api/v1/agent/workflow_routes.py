"""n8n workflow result callback (research R9).

Not a user endpoint: authenticated by an HMAC signature over the raw body using
the server-side n8n secret. Enforces tenant scope from the (signed) body and
writes an audit record for the workflow result.
"""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.config.logging_config import get_logger
from src.core.agent.entities import AuditRecord, AuditResult
from src.infrastructure.agent.n8n import get_n8n_client
from src.infrastructure.agent.persistence.repositories import AuditRepository
from src.infrastructure.database.connection import set_tenant_id
from src.infrastructure.tenancy.rls import set_tenant_context

logger = get_logger(__name__)

router = APIRouter(prefix="/agent/workflows", tags=["Agent"])


@router.post("/callback")
async def workflow_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_numu_signature: Annotated[str | None, Header()] = None,
) -> dict:
    raw = await request.body()
    if not get_n8n_client().verify_callback(raw, x_numu_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature"
        )

    try:
        data = json.loads(raw)
        tenant_id = UUID(str(data["tenant_id"]))
    except (json.JSONDecodeError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed callback body"
        )

    # Enforce tenant scope for the audit insert (RLS) — the body is HMAC-trusted.
    await set_tenant_context(db, tenant_id)
    set_tenant_id(tenant_id)

    succeeded = data.get("status") == "succeeded"
    await AuditRepository(db).add(
        AuditRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            staff_id=None,
            conversation_id=None,
            tool_name=f"workflow:{data.get('workflow')}",
            params=data.get("params") or {},
            before_state={},
            after_state=data.get("result") or {},
            result=AuditResult.APPLIED if succeeded else AuditResult.REJECTED,
        )
    )
    logger.info(
        "agent_workflow_callback",
        workflow=data.get("workflow"),
        run_id=data.get("run_id"),
        ok=succeeded,
    )
    return {"ok": True}
