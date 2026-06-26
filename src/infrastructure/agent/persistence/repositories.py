"""Tenant-scoped repositories for the Agent (RLS + explicit tenant filter).

Mirrors NUMU-api's repository style: a session-bound class with `_to_entity` /
`_to_model` mappers. Every query is additionally filtered by the active tenant
(defense-in-depth alongside RLS), reading the tenant from request context.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.agent.entities import (
    ActionProposal,
    AuditRecord,
    AuditResult,
    Conversation,
    ConversationStatus,
    ProposalStatus,
    ToolCallRecord,
    Turn,
    TurnRole,
)
from src.infrastructure.agent.persistence.models import (
    AgentActionProposalModel,
    AgentAuditLogModel,
    AgentConversationModel,
    AgentTurnModel,
)
from src.infrastructure.database.connection import get_tenant_id


def _require_tenant() -> UUID:
    tid = get_tenant_id()
    if not tid:
        raise PermissionError("No tenant context for agent persistence (fail closed)")
    return tid if isinstance(tid, UUID) else UUID(str(tid))


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _to_entity(m: AgentConversationModel) -> Conversation:
        return Conversation(
            id=m.id,
            tenant_id=m.tenant_id,
            staff_id=m.staff_id,
            status=ConversationStatus(m.status),
            title=m.title,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    async def create(self, conversation: Conversation) -> Conversation:
        tenant_id = _require_tenant()
        model = AgentConversationModel(
            id=conversation.id or uuid4(),
            tenant_id=tenant_id,
            staff_id=conversation.staff_id,
            title=conversation.title,
            status=conversation.status.value,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def get(self, conversation_id: UUID) -> Conversation | None:
        tenant_id = _require_tenant()
        result = await self.session.execute(
            select(AgentConversationModel).where(
                AgentConversationModel.id == conversation_id,
                AgentConversationModel.tenant_id == tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def list_for_staff(
        self, staff_id: UUID, *, limit: int = 50
    ) -> list[Conversation]:
        tenant_id = _require_tenant()
        result = await self.session.execute(
            select(AgentConversationModel)
            .where(
                AgentConversationModel.tenant_id == tenant_id,
                AgentConversationModel.staff_id == staff_id,
            )
            .order_by(AgentConversationModel.updated_at.desc())
            .limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]


class TurnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _to_entity(m: AgentTurnModel) -> Turn:
        tool_calls = [
            ToolCallRecord(
                name=tc.get("name", ""),
                ok=bool(tc.get("ok")),
                source=tc.get("source", []),
                error_code=tc.get("error_code"),
            )
            for tc in (m.tool_calls or [])
        ]
        return Turn(
            id=m.id,
            tenant_id=m.tenant_id,
            conversation_id=m.conversation_id,
            role=TurnRole(m.role),
            content=m.content,
            tool_calls=tool_calls,
            model_used=m.model_used,
            latency_ms=m.latency_ms,
            created_at=m.created_at,
        )

    async def add(self, turn: Turn) -> Turn:
        tenant_id = _require_tenant()
        model = AgentTurnModel(
            id=turn.id or uuid4(),
            tenant_id=tenant_id,
            conversation_id=turn.conversation_id,
            role=turn.role.value,
            content=turn.content,
            tool_calls=[
                {
                    "name": tc.name,
                    "ok": tc.ok,
                    "source": tc.source,
                    "error_code": tc.error_code,
                }
                for tc in turn.tool_calls
            ]
            or None,
            model_used=turn.model_used,
            latency_ms=turn.latency_ms,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def list_for_conversation(self, conversation_id: UUID) -> list[Turn]:
        tenant_id = _require_tenant()
        result = await self.session.execute(
            select(AgentTurnModel)
            .where(
                AgentTurnModel.tenant_id == tenant_id,
                AgentTurnModel.conversation_id == conversation_id,
            )
            .order_by(AgentTurnModel.created_at.asc())
        )
        return [self._to_entity(m) for m in result.scalars().all()]


class ProposalRepository:
    """Minimal proposal persistence (write-path lands in US2)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _to_entity(m: AgentActionProposalModel) -> ActionProposal:
        return ActionProposal(
            id=m.id,
            tenant_id=m.tenant_id,
            conversation_id=m.conversation_id,
            tool_name=m.tool_name,
            params=m.params,
            diff=m.diff,
            based_on_theme_version=m.based_on_theme_version,
            status=ProposalStatus(m.status),
            created_at=m.created_at,
        )

    async def add(self, proposal: ActionProposal) -> ActionProposal:
        tenant_id = _require_tenant()
        model = AgentActionProposalModel(
            id=proposal.id or uuid4(),
            tenant_id=tenant_id,
            conversation_id=proposal.conversation_id,
            tool_name=proposal.tool_name,
            params=proposal.params,
            diff=proposal.diff,
            based_on_theme_version=proposal.based_on_theme_version,
            status=proposal.status.value,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def get(self, proposal_id: UUID) -> ActionProposal | None:
        tenant_id = _require_tenant()
        result = await self.session.execute(
            select(AgentActionProposalModel).where(
                AgentActionProposalModel.id == proposal_id,
                AgentActionProposalModel.tenant_id == tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_pending_for_conversation(
        self, conversation_id: UUID
    ) -> ActionProposal | None:
        tenant_id = _require_tenant()
        result = await self.session.execute(
            select(AgentActionProposalModel)
            .where(
                AgentActionProposalModel.tenant_id == tenant_id,
                AgentActionProposalModel.conversation_id == conversation_id,
                AgentActionProposalModel.status == ProposalStatus.PENDING.value,
            )
            .order_by(AgentActionProposalModel.created_at.desc())
        )
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def mark(self, proposal_id: UUID, status: ProposalStatus) -> None:
        tenant_id = _require_tenant()
        result = await self.session.execute(
            select(AgentActionProposalModel).where(
                AgentActionProposalModel.id == proposal_id,
                AgentActionProposalModel.tenant_id == tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is not None:
            model.status = (
                status.value if isinstance(status, ProposalStatus) else status
            )
            await self.session.flush()


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _to_entity(m: AgentAuditLogModel) -> AuditRecord:
        return AuditRecord(
            id=m.id,
            tenant_id=m.tenant_id,
            staff_id=m.staff_id,
            tool_name=m.tool_name,
            params=m.params,
            before_state=m.before_state,
            after_state=m.after_state,
            result=AuditResult(m.result),
            conversation_id=m.conversation_id,
            model_used=m.model_used,
            created_at=m.created_at,
        )

    async def list_for_tenant(self, *, limit: int = 100) -> list[AuditRecord]:
        tenant_id = _require_tenant()
        result = await self.session.execute(
            select(AgentAuditLogModel)
            .where(AgentAuditLogModel.tenant_id == tenant_id)
            .order_by(AgentAuditLogModel.created_at.desc())
            .limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_last_applied_for_conversation(
        self, conversation_id: UUID
    ) -> AuditRecord | None:
        tenant_id = _require_tenant()
        result = await self.session.execute(
            select(AgentAuditLogModel)
            .where(
                AgentAuditLogModel.tenant_id == tenant_id,
                AgentAuditLogModel.conversation_id == conversation_id,
                AgentAuditLogModel.result == AuditResult.APPLIED.value,
            )
            .order_by(AgentAuditLogModel.created_at.desc())
        )
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def add(self, record: AuditRecord) -> AuditRecord:
        tenant_id = _require_tenant()
        model = AgentAuditLogModel(
            id=record.id or uuid4(),
            tenant_id=tenant_id,
            staff_id=record.staff_id,
            conversation_id=record.conversation_id,
            tool_name=record.tool_name,
            params=record.params,
            before_state=record.before_state,
            after_state=record.after_state,
            result=record.result.value
            if isinstance(record.result, AuditResult)
            else record.result,
            model_used=record.model_used,
        )
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        record.id = model.id
        record.created_at = model.created_at
        return record
