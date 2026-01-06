"""User repository implementation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.user import User, UserRole, UserStatus
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber
from src.infrastructure.database.models.user import UserModel


class UserRepository(IUserRepository):
    """User repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: UserModel) -> User:
        """Convert database model to domain entity."""
        return User(
            id=model.id,
            email=Email(model.email),
            hashed_password=model.hashed_password,
            first_name=model.first_name,
            last_name=model.last_name,
            role=model.role,
            status=model.status,
            phone=PhoneNumber(model.phone) if model.phone else None,
            avatar_url=model.avatar_url,
            email_verified_at=model.email_verified_at,
            last_login_at=model.last_login_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: User) -> UserModel:
        """Convert domain entity to database model."""
        return UserModel(
            id=entity.id,
            email=str(entity.email),
            hashed_password=entity.hashed_password,
            first_name=entity.first_name,
            last_name=entity.last_name,
            role=entity.role,
            status=entity.status,
            phone=str(entity.phone) if entity.phone else None,
            avatar_url=entity.avatar_url,
            email_verified_at=entity.email_verified_at,
            last_login_at=entity.last_login_at,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> User | None:
        """Get user by ID."""
        result = await self.session.execute(
            select(UserModel).where(UserModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[User]:
        """Get all users with pagination."""
        result = await self.session.execute(
            select(UserModel).offset(skip).limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: User) -> User:
        """Create a new user."""
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: User) -> User:
        """Update an existing user."""
        result = await self.session.execute(
            select(UserModel).where(UserModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.email = str(entity.email)
            model.hashed_password = entity.hashed_password
            model.first_name = entity.first_name
            model.last_name = entity.last_name
            model.role = entity.role
            model.status = entity.status
            model.phone = str(entity.phone) if entity.phone else None
            model.avatar_url = entity.avatar_url
            model.email_verified_at = entity.email_verified_at
            model.last_login_at = entity.last_login_at
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"User with id {entity.id} not found")

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a user by ID."""
        result = await self.session.execute(
            select(UserModel).where(UserModel.id == entity_id)
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of users."""
        result = await self.session.execute(
            select(UserModel)
        )
        return len(result.scalars().all())

    async def get_by_email(self, email: Email) -> User | None:
        """Get user by email."""
        return await self.get_by_email_str(str(email))

    async def get_by_email_str(self, email: str) -> User | None:
        """Get user by email string."""
        result = await self.session.execute(
            select(UserModel).where(UserModel.email == email.lower())
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def email_exists(self, email: Email) -> bool:
        """Check if email already exists."""
        result = await self.session.execute(
            select(UserModel.id).where(UserModel.email == str(email).lower())
        )
        return result.scalar_one_or_none() is not None

    async def get_by_store(
        self,
        store_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[User]:
        """Get all users associated with a store (via store ownership)."""
        from src.infrastructure.database.models.store import StoreModel
        
        result = await self.session.execute(
            select(UserModel)
            .join(StoreModel, StoreModel.owner_id == UserModel.id)
            .where(StoreModel.id == store_id)
            .offset(skip)
            .limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]
