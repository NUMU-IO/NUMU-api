"""Seed permissions catalog + system role templates, clone templates into existing tenants.

Revision ID: seed_perms_001
Revises: 2fa_stepup_001
Create Date: 2026-04-17
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "seed_perms_001"
down_revision: str | None = "2fa_stepup_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from src.core.entities.permission import PERMISSION_CATALOG
    from src.core.entities.role import SYSTEM_ROLE_TEMPLATES

    conn = op.get_bind()

    # 1. Seed permissions catalog (idempotent via code uniqueness)
    code_to_perm_id: dict[str, str] = {}
    for perm in PERMISSION_CATALOG.values():
        existing = conn.execute(
            sa.text("SELECT id FROM public.permissions WHERE code = :code"),
            {"code": perm.code},
        ).scalar_one_or_none()
        if existing:
            code_to_perm_id[perm.code] = str(existing)
            continue

        perm_id = uuid4()
        conn.execute(
            sa.text(
                """
                INSERT INTO public.permissions
                    (id, code, domain, action, qualifier, scope_type, description,
                     dependencies, risk_level, is_app, plugin_id)
                VALUES
                    (:id, :code, :domain, :action, :qualifier,
                     CAST(:scope_type AS public.permissionscopetype),
                     :description, :dependencies,
                     CAST(:risk_level AS public.permissionrisklevel),
                     :is_app, :plugin_id)
                """
            ),
            {
                "id": str(perm_id),
                "code": perm.code,
                "domain": perm.domain,
                "action": perm.action,
                "qualifier": perm.qualifier,
                "scope_type": perm.scope_type.name,
                "description": perm.description,
                "dependencies": list(perm.dependencies),
                "risk_level": perm.risk_level.name,
                "is_app": perm.is_app,
                "plugin_id": perm.plugin_id,
            },
        )
        code_to_perm_id[perm.code] = str(perm_id)

    # 2. Seed system role templates (tenant_id NULL)
    template_slug_to_role_id: dict[str, str] = {}
    for tmpl in SYSTEM_ROLE_TEMPLATES.values():
        existing = conn.execute(
            sa.text(
                "SELECT id FROM public.roles WHERE tenant_id IS NULL AND slug = :slug"
            ),
            {"slug": tmpl.slug},
        ).scalar_one_or_none()
        if existing:
            template_slug_to_role_id[tmpl.slug] = str(existing)
            continue

        role_id = uuid4()
        conn.execute(
            sa.text(
                """
                INSERT INTO public.roles
                    (id, tenant_id, name, slug, description, is_system,
                     is_owner, is_locked, version)
                VALUES
                    (:id, NULL, :name, :slug, :description, TRUE,
                     :is_owner, :is_locked, 1)
                """
            ),
            {
                "id": str(role_id),
                "name": tmpl.name,
                "slug": tmpl.slug,
                "description": tmpl.description,
                "is_owner": tmpl.is_owner,
                "is_locked": tmpl.is_locked,
            },
        )
        template_slug_to_role_id[tmpl.slug] = str(role_id)

        # Link role_permissions
        for code in tmpl.permission_ids:
            perm_id = code_to_perm_id.get(code)
            if not perm_id:
                continue
            conn.execute(
                sa.text(
                    """
                    INSERT INTO public.role_permissions
                        (id, role_id, permission_id, scope_qualifier)
                    VALUES
                        (:id, :role_id, :permission_id, :scope_qualifier)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "role_id": str(role_id),
                    "permission_id": perm_id,
                    "scope_qualifier": "{}",
                },
            )

    # 3. Clone non-Owner templates into every existing tenant that has no roles
    tenants = conn.execute(sa.text("SELECT id FROM public.tenants")).all()

    for (tenant_id,) in tenants:
        existing_count = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM public.roles "
                "WHERE tenant_id = :tid AND deleted_at IS NULL"
            ),
            {"tid": str(tenant_id)},
        ).scalar_one()
        if existing_count > 0:
            continue

        for tmpl in SYSTEM_ROLE_TEMPLATES.values():
            if tmpl.is_owner or tmpl.slug == "custom":
                continue  # Owner role is flag-based; skip "custom" empty template

            template_role_id = template_slug_to_role_id.get(tmpl.slug)
            new_role_id = uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO public.roles
                        (id, tenant_id, name, slug, description, is_system,
                         is_owner, is_locked, version, cloned_from_id)
                    VALUES
                        (:id, :tenant_id, :name, :slug, :description, FALSE,
                         FALSE, FALSE, 1, :cloned_from)
                    """
                ),
                {
                    "id": str(new_role_id),
                    "tenant_id": str(tenant_id),
                    "name": tmpl.name,
                    "slug": tmpl.slug,
                    "description": tmpl.description,
                    "cloned_from": template_role_id,
                },
            )

            for code in tmpl.permission_ids:
                perm_id = code_to_perm_id.get(code)
                if not perm_id:
                    continue
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO public.role_permissions
                            (id, role_id, permission_id, scope_qualifier)
                        VALUES
                            (:id, :role_id, :permission_id, :scope_qualifier)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "role_id": str(new_role_id),
                        "permission_id": perm_id,
                        "scope_qualifier": "{}",
                    },
                )


def downgrade() -> None:
    conn = op.get_bind()
    # Clear cloned tenant roles first (cascades to role_permissions via FK)
    conn.execute(
        sa.text(
            "DELETE FROM public.roles "
            "WHERE tenant_id IS NOT NULL AND cloned_from_id IS NOT NULL"
        )
    )
    conn.execute(sa.text("DELETE FROM public.roles WHERE tenant_id IS NULL"))
    conn.execute(sa.text("DELETE FROM public.permissions"))
