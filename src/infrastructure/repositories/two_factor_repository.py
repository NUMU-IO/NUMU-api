"""Database-backed Two-Factor Authentication repository."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.two_factor import TwoFactorAuth, TwoFactorMethod, TwoFactorStatus
from src.core.interfaces.repositories.two_factor_repository import ITwoFactorRepository
from src.infrastructure.database.models.public.two_factor import TwoFactorAuthModel


class TwoFactorRepository(ITwoFactorRepository):
    """SQLAlchemy-backed 2FA repository (public schema)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def _to_entity(self, model: TwoFactorAuthModel) -> TwoFactorAuth:
        return TwoFactorAuth(
            id=model.id,
            user_id=model.user_id,
            method=TwoFactorMethod(model.method),
            status=TwoFactorStatus(model.status),
            secret=model.secret,
            backup_codes=list(model.backup_codes or []),
            backup_codes_remaining=model.backup_codes_remaining,
            verified_at=model.verified_at,
            last_used_at=model.last_used_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: TwoFactorAuth) -> TwoFactorAuthModel:
        return TwoFactorAuthModel(
            id=entity.id,
            user_id=entity.user_id,
            method=entity.method.value,
            status=entity.status.value,
            secret=entity.secret,
            backup_codes=entity.backup_codes,
            backup_codes_remaining=entity.backup_codes_remaining,
            verified_at=entity.verified_at,
            last_used_at=entity.last_used_at,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    # ------------------------------------------------------------------
    # BaseRepository interface
    # ------------------------------------------------------------------

    async def get_by_id(self, entity_id: UUID) -> TwoFactorAuth | None:
        result = await self.session.execute(
            select(TwoFactorAuthModel).where(TwoFactorAuthModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[TwoFactorAuth]:
        result = await self.session.execute(
            select(TwoFactorAuthModel).offset(skip).limit(limit)
        )
        return [self._to_entity(m) for m in result.scalars().all()]

    async def create(self, entity: TwoFactorAuth) -> TwoFactorAuth:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: TwoFactorAuth) -> TwoFactorAuth:
        result = await self.session.execute(
            select(TwoFactorAuthModel).where(TwoFactorAuthModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"TwoFactorAuth with id {entity.id} not found")
        model.method = entity.method.value
        model.status = entity.status.value
        model.secret = entity.secret
        model.backup_codes = entity.backup_codes
        model.backup_codes_remaining = entity.backup_codes_remaining
        model.verified_at = entity.verified_at
        model.last_used_at = entity.last_used_at
        model.updated_at = entity.updated_at
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def delete(self, entity_id: UUID) -> bool:
        result = await self.session.execute(
            select(TwoFactorAuthModel).where(TwoFactorAuthModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def count(self) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(TwoFactorAuthModel)
        )
        return result.scalar_one()

    # ------------------------------------------------------------------
    # Domain-specific methods
    # ------------------------------------------------------------------

    async def get_by_user_id(self, user_id: UUID) -> TwoFactorAuth | None:
        result = await self.session.execute(
            select(TwoFactorAuthModel).where(TwoFactorAuthModel.user_id == user_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def delete_by_user_id(self, user_id: UUID) -> bool:
        result = await self.session.execute(
            select(TwoFactorAuthModel).where(TwoFactorAuthModel.user_id == user_id)
        )
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self.session.delete(model)
        await self.session.flush()
        return True

    async def user_has_2fa_enabled(self, user_id: UUID) -> bool:
        result = await self.session.execute(
            select(TwoFactorAuthModel).where(
                TwoFactorAuthModel.user_id == user_id,
                TwoFactorAuthModel.status == TwoFactorStatus.ENABLED.value,
            )
        )
        return result.scalar_one_or_none() is not None


class InMemoryTwoFactorRepository(ITwoFactorRepository):
    """Dict-backed in-memory 2FA repository (test double).

    Implements the full :class:`ITwoFactorRepository` port without a
    database, for unit-testing the 2FA use cases. Semantics deliberately
    mirror :class:`TwoFactorRepository`:

    - Reads return **detached copies** of the stored entity (the DB repo
      maps ORM rows to fresh entities on every read), so a use case that
      mutates an entity without calling ``update()`` does not silently
      persist — the same bug a real repository would expose.
    - ``update()`` raises ``ValueError`` when the entity does not exist,
      matching the DB repo's behaviour.
    - ``delete``/``delete_by_user_id`` return ``True`` only when a row
      was actually removed.
    """

    def __init__(self) -> None:
        self._items: dict[UUID, TwoFactorAuth] = {}

    @staticmethod
    def _clone(entity: TwoFactorAuth) -> TwoFactorAuth:
        return entity.model_copy(deep=True)

    # ------------------------------------------------------------------
    # BaseRepository interface
    # ------------------------------------------------------------------

    async def get_by_id(self, entity_id: UUID) -> TwoFactorAuth | None:
        entity = self._items.get(entity_id)
        return self._clone(entity) if entity else None

    async def get_all(self, skip: int = 0, limit: int = 100) -> list[TwoFactorAuth]:
        items = list(self._items.values())[skip : skip + limit]
        return [self._clone(entity) for entity in items]

    async def create(self, entity: TwoFactorAuth) -> TwoFactorAuth:
        self._items[entity.id] = self._clone(entity)
        return self._clone(entity)

    async def update(self, entity: TwoFactorAuth) -> TwoFactorAuth:
        if entity.id not in self._items:
            raise ValueError(f"TwoFactorAuth with id {entity.id} not found")
        self._items[entity.id] = self._clone(entity)
        return self._clone(entity)

    async def delete(self, entity_id: UUID) -> bool:
        return self._items.pop(entity_id, None) is not None

    async def count(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------------
    # Domain-specific methods
    # ------------------------------------------------------------------

    async def get_by_user_id(self, user_id: UUID) -> TwoFactorAuth | None:
        for entity in self._items.values():
            if entity.user_id == user_id:
                return self._clone(entity)
        return None

    async def delete_by_user_id(self, user_id: UUID) -> bool:
        for entity_id, entity in list(self._items.items()):
            if entity.user_id == user_id:
                del self._items[entity_id]
                return True
        return False

    async def user_has_2fa_enabled(self, user_id: UUID) -> bool:
        return any(
            entity.user_id == user_id and entity.status == TwoFactorStatus.ENABLED
            for entity in self._items.values()
        )
