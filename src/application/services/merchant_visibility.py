"""Which merchants the platform admin sees.

NUMU's own tenants (``is_internal``: QA, load tests, seeded showcase
brands) and Try-a-Demo tenants are not merchants. They are hidden from
every admin list, queue and number; the Merchants page lists them on their
own tab, which is the only place they appear.

Suspended merchants (``is_active`` false) are real, so they stay in lists
where staff can reactivate them, but they drop out of work queues and live
KPIs: nobody acts on a pending order from a shop that is switched off.

Defined once here so every screen counts the same population. Two forms,
because half the admin routes are raw ``text()`` SQL:

* ORM: ``visible(Model.tenant_id)`` / ``live(Model.tenant_id)``
* SQL: ``VISIBLE_SQL.format(col="o.tenant_id")`` / ``LIVE_SQL.format(...)``
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.sql.elements import ColumnElement

from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)


def is_hidden() -> ColumnElement[bool]:
    """True for a tenant row the admin never sees outside the Internal tab."""
    return or_(
        TenantModel.lifecycle_state == TenantLifecycleState.DEMO.value,
        TenantModel.is_internal.is_(True),
    )


def visible(tenant_id_col) -> ColumnElement[bool]:
    """Rows of real merchants, suspended ones included."""
    return tenant_id_col.notin_(select(TenantModel.id).where(is_hidden()))


def live(tenant_id_col) -> ColumnElement[bool]:
    """Rows of real merchants that are switched on: queues and KPIs."""
    return tenant_id_col.in_(
        select(TenantModel.id).where(~is_hidden(), TenantModel.is_active.is_(True))
    )


VISIBLE_SQL = """NOT EXISTS (
    SELECT 1 FROM public.tenants vis
    WHERE vis.id = {col}
      AND (vis.lifecycle_state = 'demo' OR vis.is_internal IS TRUE)
)"""

LIVE_SQL = """EXISTS (
    SELECT 1 FROM public.tenants vis
    WHERE vis.id = {col}
      AND vis.is_active IS TRUE
      AND vis.lifecycle_state <> 'demo'
      AND vis.is_internal IS NOT TRUE
)"""
