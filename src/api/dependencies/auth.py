"""Authentication dependencies."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.entities.user import UserRole
from src.core.exceptions import InvalidTokenError, TokenExpiredError
from src.infrastructure.external_services.token_service import token_service

security = HTTPBearer()


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> UUID:
    """Get current user ID from JWT token."""
    token = credentials.credentials

    try:
        payload = token_service.verify_token(token)
        return payload.user_id
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user_role(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> tuple[UUID, str]:
    """Get current user ID and role from JWT token."""
    token = credentials.credentials

    try:
        payload = token_service.verify_token(token)
        return payload.user_id, payload.role
    except (TokenExpiredError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_roles(*allowed_roles: UserRole):
    """Dependency factory that requires specific user roles."""

    async def role_checker(
        user_data: Annotated[tuple[UUID, str], Depends(get_current_user_role)],
    ) -> UUID:
        user_id, role = user_data

        # Convert role string to enum for comparison
        # Handle both uppercase (from DB enum name) and lowercase (from Python enum value)
        try:
            user_role = UserRole(role)
        except ValueError:
            # Try matching by name (uppercase) if value match fails
            try:
                user_role = UserRole[role.upper()]
            except KeyError:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid user role",
                )

        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )

        return user_id

    return role_checker


# Common role dependencies
require_store_owner = require_roles(UserRole.STORE_OWNER, UserRole.SUPER_ADMIN)
require_admin = require_roles(UserRole.SUPER_ADMIN)

from src.api.dependencies.repositories import (
    get_customer_repository,
    get_store_repository,
)
from src.core.entities.customer import Customer
from src.core.entities.store import Store
from src.core.interfaces.services.token_service import CustomerTokenPayload
from src.infrastructure.repositories.customer_repository import CustomerRepository
from src.infrastructure.repositories.store_repository import StoreRepository

# ... existing code ...


async def get_current_customer_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> CustomerTokenPayload:
    """Get current customer payload from JWT token."""
    token = credentials.credentials

    try:
        return token_service.verify_customer_token(token)
    except (TokenExpiredError, InvalidTokenError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_customer(
    payload: Annotated[CustomerTokenPayload, Depends(get_current_customer_payload)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
) -> Customer:
    """Get current customer from JWT token."""
    customer = await customer_repo.get_by_id(payload.customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Customer not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return customer


async def get_current_store(
    store_id: UUID,
    user_id: Annotated[UUID, Depends(require_store_owner)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> Store:
    """Get the current store, verifying ownership."""
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found",
        )
    if store.owner_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this store",
        )
    return store
