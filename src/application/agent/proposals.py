"""Apply / undo gated write proposals (US2, Constitution III).

`apply_proposal` reconstructs the proposed change against the *current* theme and
applies it through the existing theme-editor-v3 service — reusing its versioning
(snapshot) and the etag as the stale-proposal guard (FR-015). Every applied (or
rejected-at-apply) write produces exactly one immutable audit record (FR-010).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import lru_cache
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


class PermissionDeniedError(ProposalError):
    """Caller lacks the permission the proposal's own tool requires."""


# Confirm used to gate every proposal on a single hardcoded "themes.edit".
# That is wrong in both directions once more than one kind of write exists: a
# theme editor with no product rights could confirm a price change, and a
# product manager without themes.edit was refused their own. Each CONFIRM-tier
# spec already declares `required_permission`, so ask the registry.
#
# Fails closed: an unknown tool, or one declaring no permission, still needs the
# strictest gate rather than none.
_FALLBACK_PERMISSION = "themes.edit"


@lru_cache(maxsize=1)
def _registry():
    # Imported lazily: tool modules import from this package, so a module-level
    # import here would close the cycle.
    from src.application.agent.tool_registry import build_default_registry

    return build_default_registry()


def permission_for_tool(tool_name: str) -> str:
    spec = _registry().get(tool_name)
    if spec is None or not spec.required_permission:
        return _FALLBACK_PERMISSION
    return spec.required_permission


async def _require_tool_permission(has_permission, tool_name: str) -> None:
    """Re-check at apply time — never trust the proposal step alone."""
    if has_permission is None:
        return
    needed = permission_for_tool(tool_name)
    if not await has_permission(needed):
        raise PermissionDeniedError("forbidden", f"Missing permission: {needed}")


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


async def _apply_update_product(
    session, *, store_id: UUID, staff_id: UUID, params: dict
) -> dict:
    from src.application.dto.product import UpdateProductDTO
    from src.application.use_cases.products.update_product import (
        UpdateProductUseCase,
    )
    from src.infrastructure.repositories.product_repository import ProductRepository

    product_repo = ProductRepository(session)
    product_id = UUID(str(params["product_id"]))

    # Re-fetch at apply time: the audit's before_state must reflect what the
    # values actually were when applied (they may have drifted since propose).
    current = await product_repo.get_by_id(product_id)
    if current is None or current.store_id != store_id:
        raise ProposalError("not_found", "Product no longer exists in this store.")
    before_state = {
        "product_id": str(product_id),
        "price": str(current.price.amount),
        "compare_at_price": (
            str(current.compare_at_price.amount) if current.compare_at_price else None
        ),
        "quantity": current.quantity,
    }

    dto = UpdateProductDTO(
        price=_to_decimal(params["price"]) if params.get("price") is not None else None,
        compare_at_price=(
            _to_decimal(params["compare_at_price"])
            if params.get("compare_at_price") is not None
            else None
        ),
        quantity=params.get("quantity"),
    )
    use_case = UpdateProductUseCase(
        product_repository=product_repo,
        store_repository=StoreRepository(session),
    )
    updated = await use_case.execute(product_id, dto, staff_id)
    return {
        "summary": f"Updated product {updated.name}",
        "after_state": {
            "product_id": str(product_id),
            "price": str(updated.price),
            "compare_at_price": (
                str(updated.compare_at_price) if updated.compare_at_price else None
            ),
            "quantity": updated.quantity,
        },
        "before_state": before_state,
        "result": {"product_id": str(product_id), "name": updated.name},
    }


async def _undo_create_discount(
    session, *, store_id: UUID, staff_id: UUID, audit
) -> dict:
    """Undo a created coupon by deleting it."""
    from src.application.use_cases.coupons.delete_coupon import DeleteCouponUseCase

    coupon_id = (audit.after_state or {}).get("coupon_id")
    if not coupon_id:
        raise NothingToUndoError("nothing_to_undo", "No coupon recorded to remove.")
    use_case = DeleteCouponUseCase(
        coupon_repository=CouponRepository(session),
        store_repository=StoreRepository(session),
    )
    await use_case.execute(UUID(coupon_id), staff_id)
    return {"undid": "create_discount", "deleted_coupon_id": coupon_id}


async def _undo_update_product(
    session, *, store_id: UUID, staff_id: UUID, audit
) -> dict:
    """Undo a product update by restoring the audited before values."""
    from src.application.dto.product import UpdateProductDTO
    from src.application.use_cases.products.update_product import (
        UpdateProductUseCase,
    )
    from src.infrastructure.repositories.product_repository import ProductRepository

    before = audit.before_state or {}
    product_id = before.get("product_id")
    if not product_id:
        raise NothingToUndoError("nothing_to_undo", "No prior product state recorded.")
    dto = UpdateProductDTO(
        price=_to_decimal(before["price"]) if before.get("price") is not None else None,
        compare_at_price=(
            _to_decimal(before["compare_at_price"])
            if before.get("compare_at_price") is not None
            else None
        ),
        quantity=before.get("quantity"),
    )
    use_case = UpdateProductUseCase(
        product_repository=ProductRepository(session),
        store_repository=StoreRepository(session),
    )
    await use_case.execute(UUID(product_id), dto, staff_id)
    return {"undid": "update_product", "product_id": product_id}


async def _apply_send_cart_recovery(
    session, *, store_id: UUID, staff_id: UUID, params: dict
) -> dict:
    """Send the recovery email + stamp the checkout (mirrors the dashboard route)."""
    from datetime import UTC, datetime

    from src.core.interfaces.services.email_service import EmailMessage
    from src.infrastructure.external_services.resend.email_service import (
        ResendEmailService,
    )
    from src.infrastructure.repositories import AbandonedCheckoutRepository

    repo = AbandonedCheckoutRepository(session)
    checkout_id = UUID(str(params["checkout_id"]))
    checkout = await repo.get_by_id(checkout_id)
    if checkout is None or checkout.store_id != store_id:
        raise ProposalError("not_found", "Checkout no longer exists in this store.")
    if checkout.recovered_at is not None:
        raise ProposalError("already_recovered", "Checkout was already recovered.")
    if not checkout.email:
        raise ProposalError("no_email", "Checkout has no email address.")

    store = await StoreRepository(session).get_by_id(store_id)
    store_name = store.name if store else "your store"

    items_html = "".join(
        f"<li>{(li.get('product_name') or 'Item')} × {li.get('quantity', 1)}</li>"
        for li in checkout.line_items
    )
    html = (
        f"<p>Hi there,</p>"
        f"<p>You left items in your cart at <strong>{store_name}</strong>.</p>"
        f"<ul>{items_html}</ul>"
        f"<p>Come back and finish your order whenever you're ready.</p>"
    )
    await ResendEmailService().send_email(
        EmailMessage(
            to=str(checkout.email),
            subject=f"Complete your order at {store_name}",
            html_content=html,
        )
    )
    sent_at = datetime.now(UTC)
    await repo.mark_recovery_email_sent(checkout_id, sent_at)
    return {
        "summary": "Sent cart recovery email",
        "after_state": {
            "checkout_id": str(checkout_id),
            "recovery_email_sent_at": sent_at.isoformat(),
        },
        "result": {"checkout_id": str(checkout_id), "sent": True},
    }


async def _undo_send_cart_recovery(
    session, *, store_id: UUID, staff_id: UUID, audit
) -> dict:
    """A sent email cannot be unsent — fail with an honest message.

    Registered anyway so this audit can never fall through to the
    theme-restore path (see ACTION_UNDOERS note below).
    """
    raise NothingToUndoError("cannot_undo", "A sent recovery email can't be unsent.")


async def _apply_create_product(
    session, *, store_id: UUID, staff_id: UUID, params: dict
) -> dict:
    """Create the proposed product through the same use case the API route runs.

    Drafts by default (the tool sets it): a model that misheard a price must not
    be able to put a live, buyable product in front of shoppers on one
    confirmation. Publishing stays a deliberate act in the dashboard.
    """
    from src.application.dto.product import CreateProductDTO
    from src.application.use_cases.products.create_product import (
        CreateProductUseCase,
    )
    from src.infrastructure.repositories.category_repository import (
        CategoryRepository,
    )
    from src.infrastructure.repositories.product_repository import ProductRepository

    dto = CreateProductDTO(
        name=str(params["name"]),
        price=_to_decimal(params["price"]),
        compare_at_price=(
            _to_decimal(params["compare_at_price"])
            if params.get("compare_at_price") is not None
            else None
        ),
        quantity=int(params.get("quantity") or 0),
        description=params.get("description"),
        category_id=(
            UUID(str(params["category_id"])) if params.get("category_id") else None
        ),
        images=list(params.get("images") or []),
        status=params.get("status") or "draft",
    )
    use_case = CreateProductUseCase(
        product_repository=ProductRepository(session),
        store_repository=StoreRepository(session),
        category_repository=CategoryRepository(session),
    )
    created = await use_case.execute(dto, store_id, staff_id)
    return {
        "summary": f"Created product {created.name}",
        "after_state": {
            "product_id": str(created.id),
            "name": created.name,
            "status": getattr(created.status, "value", str(created.status)),
        },
        "result": {"product_id": str(created.id), "name": created.name},
    }


async def _undo_create_product(
    session, *, store_id: UUID, staff_id: UUID, audit
) -> dict:
    """Undo a creation by deleting the product it created."""
    from src.application.use_cases.products.delete_product import (
        DeleteProductUseCase,
    )
    from src.infrastructure.repositories.product_repository import ProductRepository

    after = audit.after_state or {}
    product_id = after.get("product_id")
    if not product_id:
        raise NothingToUndoError("nothing_to_undo", "No created product recorded.")

    use_case = DeleteProductUseCase(
        product_repository=ProductRepository(session),
        store_repository=StoreRepository(session),
    )
    await use_case.execute(UUID(str(product_id)), staff_id, store_id)
    return {
        "summary": f"Deleted product {after.get('name') or product_id}",
        "after_state": {},
    }


# tool_name → applier. Tools listed here follow the generic (non-theme) path.
ACTION_APPLIERS = {
    "create_discount": _apply_create_discount,
    "create_product": _apply_create_product,
    "update_product": _apply_update_product,
    "send_cart_recovery": _apply_send_cart_recovery,
}

# tool_name → undoer for applied action audits. Anything not listed here that
# reaches undo_last follows the theme-restore path, so EVERY action applier must
# have an entry (else its audit's before_state would be pushed into the theme).
ACTION_UNDOERS = {
    "create_discount": _undo_create_discount,
    "create_product": _undo_create_product,
    "update_product": _undo_update_product,
    "send_cart_recovery": _undo_send_cart_recovery,
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
            # Appliers report before_state when the action mutates existing data
            # (e.g. update_product) — that is what makes the action undo-capable.
            before_state=outcome.get("before_state", {}),
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
    has_permission=None,
) -> dict:
    proposal_repo = ProposalRepository(session)
    audit_repo = AuditRepository(session)

    proposal = await proposal_repo.get(proposal_id)
    if proposal is None:
        raise ProposalError("not_found", "Proposal not found.")
    if proposal.status != ProposalStatus.PENDING:
        raise ProposalError("already_resolved", f"Proposal is {proposal.status.value}.")

    await _require_tool_permission(has_permission, proposal.tool_name)

    # Confirm takes its store from the URL. Without this a tenant with two
    # stores could propose against A and confirm at /stores/B/agent/confirm,
    # and the change would land on B — the params were built against A's draft.
    # A proposal predating the column has no store to check against, so it is
    # refused rather than trusted.
    if proposal.store_id != store_id:
        raise ProposalError(
            "wrong_store", "This proposal was made for a different store."
        )

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

    # Deliberately NOT published. A confirmed agent change lands in the same
    # draft the customizer edits; the merchant presses Update when they have
    # looked at it. Publishing here was the only path where a model-initiated
    # change reached shoppers with no human in between, and it contradicted the
    # blast radius this feature was signed off on ("the store's draft only").
    # autosave_draft commits, which drops the transaction-scoped
    # app.current_tenant GUC; re-apply it so the writes below pass FORCE RLS.
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
        "published": False,
        "proposal_id": str(proposal_id),
        "audit_id": str(audit.id),
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
    has_permission=None,
) -> dict:
    audit_repo = AuditRepository(session)
    last = await audit_repo.get_last_applied_for_conversation(conversation_id)
    if last is None:
        raise NothingToUndoError(
            "nothing_to_undo", "There is no applied change to undo."
        )

    # Action audits (Pillar 2) undo through their registered undoer — NEVER the
    # theme path (their before_state is domain data, not a theme draft).
    if last.tool_name in ACTION_UNDOERS:
        outcome = await ACTION_UNDOERS[last.tool_name](
            session, store_id=store_id, staff_id=staff_id, audit=last
        )
        audit = await audit_repo.add(
            AuditRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                staff_id=staff_id,
                conversation_id=conversation_id,
                tool_name="undo",
                params={"undid_audit_id": str(last.id), **outcome},
                # Deliberately empty: if this undo record ever becomes "last",
                # the `not before_state` guard below stops a second undo instead
                # of pushing action data through the theme-restore path.
                before_state={},
                after_state=last.before_state,
                result=AuditResult.APPLIED,
                model_used=model_used,
            )
        )
        logger.info(
            "agent_action_undone",
            conversation_id=str(conversation_id),
            tool=last.tool_name,
            audit_id=str(audit.id),
        )
        return {"undone": True, "audit_id": str(audit.id)}

    if not last.before_state:
        raise NothingToUndoError(
            "nothing_to_undo", "There is no applied change to undo."
        )

    # Reversing a write needs the same permission the write itself needed.
    await _require_tool_permission(has_permission, last.tool_name)

    service = build_v3_service(session)
    draft_res = await service.get_draft_with_etag(store_id)

    await service.autosave_draft(
        store_id=store_id,
        payload=dict(last.before_state),
        user_id=staff_id,
        change_summary="Agent: undo last change",
        expected_etag=draft_res.get("etag"),
    )
    # Not published, for the same reason apply_proposal does not: undo restores
    # the draft and the merchant decides when it goes live.
    await set_tenant_context(
        session, tenant_id
    )  # re-apply RLS GUC dropped by autosave's commit

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
