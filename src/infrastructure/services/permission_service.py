"""Permission service for effective permission resolution."""

import fnmatch
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.permission import (
    resolve_dependencies,
)
from src.core.entities.tenant_membership import EffectivePermissions
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.public.membership_override import (
    MembershipOverrideModel,
    MembershipRoleModel,
    OverrideEffect,
)
from src.infrastructure.database.models.public.permission import PermissionModel
from src.infrastructure.database.models.public.role import (
    RoleModel,
    RolePermissionModel,
)
from src.infrastructure.database.models.public.temporary_access_grant import (
    TemporaryAccessGrantModel,
)
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)


class PermissionService:
    """Service for resolving effective permissions."""

    def __init__(
        self,
        session: AsyncSession,
        cache: RedisCacheService | None = None,
    ) -> None:
        self.session = session
        self.cache = cache

    async def get_effective_permissions(
        self,
        membership: TenantMembershipModel,
    ) -> EffectivePermissions:
        """Get effective permissions for a membership.

        Resolution order (low → high):
        1. Role-derived ALLOWs
        2. Temporary grants
        3. Override ALLOWs
        4. Override DENYs
        5. Owner short-circuit
        """
        cache_key = f"perms:{membership.tenant_id}:{membership.user_id}:v{membership.permission_version}"

        if self.cache:
            cached = await self.cache.get(cache_key)
            if cached:
                return self._deserialize_effective(cache_key, cached)

        result = await self._compute_effective_permissions(membership)

        if self.cache:
            await self.cache.set(
                cache_key,
                json.dumps(result.to_dict()),
                expire=86400,
            )

        return result

    async def _compute_effective_permissions(
        self,
        membership: TenantMembershipModel,
    ) -> EffectivePermissions:
        """Compute effective permissions from DB."""
        allowed: set[str] = set()
        denied: set[str] = set()
        wildcards: set[str] = set()
        scopes: dict[str, list[dict]] = {}

        if membership.is_owner:
            return EffectivePermissions(
                tenant_id=str(membership.tenant_id),
                user_id=str(membership.user_id),
                membership_id=str(membership.id),
                is_owner=True,
                allowed=frozenset({"*"}),
                wildcards=frozenset({"*"}),
                denied=frozenset(),
                scopes={},
                version=membership.permission_version,
            )

        roles_result = await self.session.execute(
            select(RoleModel)
            .join(MembershipRoleModel, MembershipRoleModel.role_id == RoleModel.id)
            .where(MembershipRoleModel.membership_id == membership.id)
        )
        roles = list(roles_result.scalars().all())

        for role in roles:
            perms_result = await self.session.execute(
                select(RolePermissionModel).where(
                    RolePermissionModel.role_id == role.id
                )
            )
            role_perms = list(perms_result.scalars().all())

            for rp in role_perms:
                perm_result = await self.session.execute(
                    select(PermissionModel).where(
                        PermissionModel.id == rp.permission_id
                    )
                )
                perm = perm_result.scalar_one_or_none()
                if perm:
                    if "*" in perm.code:
                        wildcards.add(perm.code.rstrip("*"))
                    else:
                        allowed.add(perm.code)
                        if rp.scope_qualifier:
                            scopes[perm.code] = [rp.scope_qualifier]

        temp_result = await self.session.execute(
            select(TemporaryAccessGrantModel).where(
                TemporaryAccessGrantModel.membership_id == membership.id,
                TemporaryAccessGrantModel.valid_from <= datetime.utcnow(),
                TemporaryAccessGrantModel.valid_until > datetime.utcnow(),
                TemporaryAccessGrantModel.revoked_at.is_(None),
            )
        )
        temp_grants = list(temp_result.scalars().all())

        for grant in temp_grants:
            role_perms_result = await self.session.execute(
                select(RolePermissionModel).where(
                    RolePermissionModel.role_id == grant.role_id
                )
            )
            for rp in role_perms_result.scalars().all():
                perm_result = await self.session.execute(
                    select(PermissionModel).where(
                        PermissionModel.id == rp.permission_id
                    )
                )
                perm = perm_result.scalar_one_or_none()
                if perm:
                    allowed.add(perm.code)

        allow_overrides_result = await self.session.execute(
            select(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == membership.id,
                MembershipOverrideModel.effect == OverrideEffect.ALLOW,
            )
        )
        for override in allow_overrides_result.scalars().all():
            perm_result = await self.session.execute(
                select(PermissionModel).where(
                    PermissionModel.id == override.permission_id
                )
            )
            perm = perm_result.scalar_one_or_none()
            if perm:
                allowed.add(perm.code)
                if override.scope_qualifier:
                    scopes[perm.code] = [override.scope_qualifier]

        deny_overrides_result = await self.session.execute(
            select(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == membership.id,
                MembershipOverrideModel.effect == OverrideEffect.DENY,
            )
        )
        for override in deny_overrides_result.scalars().all():
            perm_result = await self.session.execute(
                select(PermissionModel).where(
                    PermissionModel.id == override.permission_id
                )
            )
            perm = perm_result.scalar_one_or_none()
            if perm:
                denied.add(perm.code)

        final_allowed = set(allowed) - denied

        for code in list(final_allowed):
            deps = resolve_dependencies(code)
            final_allowed.update(deps)

        return EffectivePermissions(
            tenant_id=str(membership.tenant_id),
            user_id=str(membership.user_id),
            membership_id=str(membership.id),
            is_owner=membership.is_owner,
            allowed=frozenset(final_allowed),
            wildcards=frozenset(wildcards),
            denied=frozenset(denied),
            scopes=scopes,
            version=membership.permission_version,
        )

    def _deserialize_effective(
        self,
        key: str,
        data: dict | str,
    ) -> EffectivePermissions:
        """Deserialize effective permissions from cache."""
        payload: dict = json.loads(data) if isinstance(data, str) else data
        return EffectivePermissions(
            tenant_id=payload["tenant_id"],
            user_id=payload["user_id"],
            membership_id=payload["membership_id"],
            is_owner=payload["is_owner"],
            allowed=frozenset(payload["allowed"]),
            wildcards=frozenset(payload["wildcards"]),
            denied=frozenset(payload["denied"]),
            scopes=payload["scopes"],
            version=payload["version"],
        )

    async def has_permission(
        self,
        membership: TenantMembershipModel,
        code: str,
    ) -> bool:
        """Check if membership has a permission."""
        effective = await self.get_effective_permissions(membership)
        return effective.has_permission(code)

    def _matches_wildcard(self, code: str, wildcard: str) -> bool:
        """Check if code matches wildcard pattern."""
        return fnmatch.fnmatch(code, wildcard + "*") or code.startswith(wildcard)


class PermissionCatalog:
    """In-memory permission catalog with caching."""

    _catalog: dict[str, PermissionModel] = {}
    _loaded: bool = False

    @classmethod
    def load(cls, permissions: list[PermissionModel]) -> None:
        """Load permissions into catalog."""
        cls._catalog = {p.code: p for p in permissions}
        cls._loaded = True

    @classmethod
    def get(cls, code: str) -> PermissionModel | None:
        """Get permission by code."""
        return cls._catalog.get(code)

    @classmethod
    def get_by_domain(cls, domain: str) -> list[PermissionModel]:
        """Get permissions by domain."""
        return [p for p in cls._catalog.values() if p.domain == domain]

    @classmethod
    def all(cls) -> list[PermissionModel]:
        """Get all permissions."""
        return list(cls._catalog.values())
