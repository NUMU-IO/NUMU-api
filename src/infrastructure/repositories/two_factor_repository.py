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
