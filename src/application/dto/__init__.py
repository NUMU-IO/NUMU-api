"""Application DTOs module."""

from src.application.dto.auth import (
    AuthResponseDTO,
    ChangePasswordDTO,
    LoginDTO,
    PasswordResetDTO,
    PasswordResetRequestDTO,
    RefreshTokenDTO,
    RegisterDTO,
    TokenDTO,
)
from src.application.dto.base import BaseDTO, PaginatedDTO
from src.application.dto.cart import (
    AddToCartDTO,
    CartDTO,
    CartItemDTO,
    CartOperationResultDTO,
    RemoveFromCartDTO,
    UpdateCartItemDTO,
)
from src.application.dto.product import CreateProductDTO, ProductDTO, UpdateProductDTO
from src.application.dto.store import CreateStoreDTO, StoreDTO, UpdateStoreDTO
from src.application.dto.user import CreateUserDTO, UpdateUserDTO, UserDTO

__all__ = [
    "BaseDTO",
    "PaginatedDTO",
    # User
    "UserDTO",
    "CreateUserDTO",
    "UpdateUserDTO",
    # Auth
    "LoginDTO",
    "TokenDTO",
    "AuthResponseDTO",
    "RegisterDTO",
    "RefreshTokenDTO",
    "PasswordResetRequestDTO",
    "PasswordResetDTO",
    "ChangePasswordDTO",
    # Store
    "StoreDTO",
    "CreateStoreDTO",
    "UpdateStoreDTO",
    # Product
    "ProductDTO",
    "CreateProductDTO",
    "UpdateProductDTO",
    # Cart
    "CartDTO",
    "CartItemDTO",
    "AddToCartDTO",
    "UpdateCartItemDTO",
    "RemoveFromCartDTO",
    "CartOperationResultDTO",
]
