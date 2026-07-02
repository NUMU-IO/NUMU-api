"""Apply / undo gated write proposals (US2, Constitution III).

`apply_proposal` reconstructs the proposed change against the *current* theme and
applies it through the existing theme-editor-v3 service — reusing its versioning
(snapshot) and the etag as the stale-proposal guard (FR-015). Every applied (or
rejected-at-apply) write produces exactly one immutable audit record (FR-010).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from src.application.dto.coupon import CreateCouponDTO
from src.application.services.theme_v3_service import StaleEtagError
from src.application.use_cases.coupons.create_coupon import CreateCouponUseCase
from src.core.agent.entities import (
    AuditRecord,
    AuditResult,
    ProposalStatus,
)
from src.core.exceptions import (
    AuthorizationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ValidationError,
)
from src.core.logging import get_logger
from src.infrastructure.agent.persistence.repositories import (
    AuditRepository,
    ProposalRepository,
)
from src.infrastructure.agent.tools._theme_common import build_v3_service
from src.infrastructure.repositories.coupon_repository import CouponRepository
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.repositories.store_theme_repository import StoreThemeRepository
from src.infrastructure.tenancy.rls import set_tenant_context

logger = get_logger(__name__)


class ProposalError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StaleProposalError(ProposalError):
    """Theme changed since the proposal was generated — re-preview required."""


class NothingToUndoError(ProposalError):
    pass


_SUPPORTED_WRITE_TOOLS = ("add_theme_section", "update_theme_setting")


def _insert_section(draft: dict, params: dict) -> dict:
    """Reconstruct the proposed section insertion against the current draft."""
    page = params["page"]
    new_id = params["new_section_id"]
    templates = draft.setdefault("templates", {})
    if page not in templates:
        raise ProposalError("page_gone", f"Page '{page}' no longer exists.")
    tpl = templates[page]
    tpl.setdefault("sections", {})[new_id] = {
        "type": params["section_type"],
        "disabled": False,
        "settings": params.get("settings") or {},
        "blocks": {},
        "block_order": [],
    }
    order = tpl.setdefault("order", [])
    pos = params.get("position")
    if not isinstance(pos, int) or pos < 0 or pos > len(order):
        pos = len(order)
    order.insert(pos, new_id)
    return draft


def _set_setting(draft: dict, params: dict) -> dict:
    """Apply an update_theme_setting change against the current draft (US3)."""
    path = params["setting_path"]
    value = params["value"]
    parts = path.split(".")
    if parts and parts[0] == "global":
        draft.setdefault("global_settings", {})[".".join(parts[1:])] = value
        return draft
    if len(parts) < 3:
        raise ProposalError("bad_path", f"Invalid setting path '{path}'.")
    page, section_id, key = parts[0], parts[1], ".".join(parts[2:])
    sections = ((draft.get("templates") or {}).get(page) or {}).get("sections") or {}
    if section_id not in sections:
        raise ProposalError("section_gone", f"Section '{section_id}' no longer exists.")
    sections[section_id].setdefault("settings", {})[key] = value
    return draft


def _apply_change(tool_name: str, draft: dict, params: dict) -> dict:
    if tool_name == "add_theme_section":
        return _insert_section(draft, params)
    if tool_name == "update_theme_setting":
        return _set_setting(draft, params)
    raise ProposalError("unsupported", f"Cannot apply tool '{tool_name}'.")


def _change_summary(tool_name: str, params: dict) -> str:
    if tool_name == "add_theme_section":
        return f"Agent: add {params['section_type']} to {params['page']}"
    return f"Agent: update {params.get('setting_path')}"


# ── Non-theme actions (Pillar 2) ─────────────────────────────────────────────
# Action tools have no theme draft/publish semantics; each maps its confirmed
# proposal to a use-case that performs the real change. An applier returns the
# audit `after_state`, a human summary, and the data surfaced back to the caller.


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProposalError("bad_params", f"Invalid numeric value: {value!r}") from exc


async def _apply_create_discount(
    session, *, store_id: UUID, staff_id: UUID, params: dict
) -> dict:
    use_case = CreateCouponUseCase(
        coupon_repository=CouponRepository(session),
        store_repository=StoreRepository(session),
    )
    dto = CreateCouponDTO(
        code=params["code"],
        coupon_type=params["discount_type"],
        value=_to_decimal(params["value"]),
        min_order_amount=(
            _to_decimal(params["min_order_amount"])
            if params.get("min_order_amount") is not None
            else None
        ),
        usage_limit=params.get("usage_limit"),
    )
    coupon = await use_case.execute(dto, store_id, staff_id)
    return {
        "summary": f"Created coupon {coupon.code}",
        "after_state": {
            "coupon_id": str(coupon.id),
            "code": coupon.code,
            "type": coupon.coupon_type,
            "value": str(coupon.value),
        },
        "result": {"coupon_id": str(coupon.id), "code": coupon.code},
    }


# tool_name → applier. Tools listed here follow the generic (non-theme) path.
ACTION_APPLIERS = {
    "create_discount": _apply_create_discount,
}


async def _apply_action_proposal(
    session,
    *,
    proposal,
    store_id: UUID,
    staff_id: UUID,
    tenant_id: UUID,
    model_used: str | None,
    proposal_repo: ProposalRepository,
    audit_repo: AuditRepository,
) -> dict:
    """Apply a non-theme action proposal via its registered use-case applier."""
    applier = ACTION_APPLIERS[proposal.tool_name]
    conversation_id = proposal.conversation_id
    try:
        outcome = await applier(
            session, store_id=store_id, staff_id=staff_id, params=proposal.params
        )
    except (
        ValidationError,
        AuthorizationError,
        EntityNotFoundError,
        EntityAlreadyExistsError,
    ) as exc:
        # Domain rejection at apply time is still one immutable audit record.
        await audit_repo.add(
            AuditRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                staff_id=staff_id,
                conversation_id=conversation_id,
                tool_name=proposal.tool_name,
                params=proposal.params,
                before_state={},
                after_state={},
                result=AuditResult.REJECTED,
                model_used=model_used,
            )
        )
        await proposal_repo.mark(proposal.id, ProposalStatus.DECLINED)
        raise ProposalError("apply_failed", str(exc)) from None

    audit = await audit_repo.add(
        AuditRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            staff_id=staff_id,
            conversation_id=conversation_id,
            tool_name=proposal.tool_name,
            params=proposal.params,
            before_state={},
            after_state=outcome["after_state"],
            result=AuditResult.APPLIED,
            model_used=model_used,
        )
    )
    await proposal_repo.mark(proposal.id, ProposalStatus.APPLIED)
    logger.info(
        "agent_action_applied",
        proposal_id=str(proposal.id),
        tool=proposal.tool_name,
        audit_id=str(audit.id),
    )
    return {
        "applied": True,
        "proposal_id": str(proposal.id),
        "audit_id": str(audit.id),
        **outcome.get("result", {}),
    }


async def apply_proposal(
    session,
    *,
    store_id: UUID,
    staff_id: UUID,
    tenant_id: UUID,
    conversation_id: UUID | None,
    proposal_id: UUID,
    model_used: str | None = None,
) -> dict:
    proposal_repo = ProposalRepository(session)
    audit_repo = AuditRepository(session)

    proposal = await proposal_repo.get(proposal_id)
    if proposal is None:
        raise ProposalError("not_found", "Proposal not found.")
    if proposal.status != ProposalStatus.PENDING:
        raise ProposalError("already_resolved", f"Proposal is {proposal.status.value}.")

    # Non-theme actions (Pillar 2) take the generic use-case apply path.
    if proposal.tool_name in ACTION_APPLIERS:
        return await _apply_action_proposal(
            session,
            proposal=proposal,
            store_id=store_id,
            staff_id=staff_id,
            tenant_id=tenant_id,
            model_used=model_used,
            proposal_repo=proposal_repo,
            audit_repo=audit_repo,
        )

    if proposal.tool_name not in _SUPPORTED_WRITE_TOOLS:
        raise ProposalError("unsupported", f"Cannot apply tool '{proposal.tool_name}'.")

    # Link the audit to the proposal's own conversation (ignore any passed value).
    conversation_id = proposal.conversation_id

    store_theme_repo = StoreThemeRepository(session)
    store_theme = await store_theme_repo.get_active_for_store(store_id)
    if store_theme is None:
        raise ProposalError("no_theme", "Store has no active theme.")
    before_state = dict(store_theme.customization_v3 or {})

    service = build_v3_service(session)
    draft_res = await service.get_draft_with_etag(store_id)
    draft = dict(draft_res.get("draft") or {})

    draft = _apply_change(proposal.tool_name, draft, proposal.params)

    summary = _change_summary(proposal.tool_name, proposal.params)
    try:
        # expected_etag = the version the proposal was based on → stale guard (FR-015).
        await service.autosave_draft(
            store_id=store_id,
            payload=draft,
            user_id=staff_id,
            change_summary=summary,
            expected_etag=proposal.based_on_theme_version,
        )
    except StaleEtagError:
        await audit_repo.add(
            AuditRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                staff_id=staff_id,
                conversation_id=conversation_id,
                tool_name=proposal.tool_name,
                params=proposal.params,
                before_state=before_state,
                after_state={},
                result=AuditResult.REJECTED,
                model_used=model_used,
            )
        )
        await proposal_repo.mark(proposal_id, ProposalStatus.EXPIRED)
        raise StaleProposalError(
            "stale_proposal",
            "The theme changed since this was proposed. Please re-preview.",
        ) from None

    published = await service.publish(store_id=store_id, user_id=staff_id)

    # publish() commits, which drops the transaction-scoped app.current_tenant GUC;
    # re-apply it so the audit/proposal writes below pass FORCE RLS.
    await set_tenant_context(session, tenant_id)

    audit = await audit_repo.add(
        AuditRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            staff_id=staff_id,
            conversation_id=conversation_id,
            tool_name=proposal.tool_name,
            params=proposal.params,
            before_state=before_state,
            after_state={"diff": proposal.diff},
            result=AuditResult.APPLIED,
            model_used=model_used,
        )
    )
    await proposal_repo.mark(proposal_id, ProposalStatus.APPLIED)

    logger.info(
        "agent_proposal_applied", proposal_id=str(proposal_id), audit_id=str(audit.id)
    )
    return {
        "applied": True,
        "proposal_id": str(proposal_id),
        "audit_id": str(audit.id),
        "revision_id": published.get("revision_id"),
    }


async def decline_proposal(session, *, proposal_id: UUID) -> dict:
    proposal_repo = ProposalRepository(session)
    proposal = await proposal_repo.get(proposal_id)
    if proposal is None:
        raise ProposalError("not_found", "Proposal not found.")
    if proposal.status == ProposalStatus.PENDING:
        await proposal_repo.mark(proposal_id, ProposalStatus.DECLINED)
    return {"declined": True, "proposal_id": str(proposal_id)}


async def undo_last(
    session,
    *,
    store_id: UUID,
    staff_id: UUID,
    tenant_id: UUID,
    conversation_id: UUID,
    model_used: str | None = None,
) -> dict:
    audit_repo = AuditRepository(session)
    last = await audit_repo.get_last_applied_for_conversation(conversation_id)
    if last is None or not last.before_state:
        raise NothingToUndoError(
            "nothing_to_undo", "There is no applied change to undo."
        )

    service = build_v3_service(session)
    draft_res = await service.get_draft_with_etag(store_id)

    await service.autosave_draft(
        store_id=store_id,
        payload=dict(last.before_state),
        user_id=staff_id,
        change_summary="Agent: undo last change",
        expected_etag=draft_res.get("etag"),
    )
    await service.publish(store_id=store_id, user_id=staff_id)
    await set_tenant_context(
        session, tenant_id
    )  # re-apply RLS GUC dropped by publish's commit

    audit = await audit_repo.add(
        AuditRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            staff_id=staff_id,
            conversation_id=conversation_id,
            tool_name="undo",
            params={"undid_audit_id": str(last.id)},
            before_state=last.after_state,
            after_state=last.before_state,
            result=AuditResult.APPLIED,
            model_used=model_used,
        )
    )
    logger.info(
        "agent_proposal_undone",
        conversation_id=str(conversation_id),
        audit_id=str(audit.id),
    )
    return {"undone": True, "audit_id": str(audit.id)}
