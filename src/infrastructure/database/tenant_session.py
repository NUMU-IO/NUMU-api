"""``tenant_session(tenant_id)`` — context manager alias for the existing
``RLSContext``. Exists for naming consistency with the spec / task list
(TASK-SEC-005); behavior identical to the canonical helper.

Usage::

    async with tenant_session(session, tenant_id):
        # All queries inside RLS-filter by tenant_id
        ...

The Celery WhatsApp tasks (whatsapp_scheduled_send_dispatcher,
whatsapp_dead_letter_purge, etc.) MUST wrap every per-store DB operation
in this context manager so RLS is enforced even though there is no HTTP
request to set ``app.current_tenant`` automatically.
"""

from src.infrastructure.tenancy.rls import RLSContext

# Public alias. Imports of ``tenant_session`` work identically to
# ``RLSContext`` since this is a re-export.
tenant_session = RLSContext

__all__ = ["tenant_session"]
