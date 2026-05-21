"""Application use cases module."""

from src.application.use_cases.auth import (
    GetCurrentUserUseCase,
    LoginUserUseCase,
    RefreshTokenUseCase,
    RegisterUserUseCase,
)
from src.application.use_cases.coupons import (
    ApplyCouponUseCase,
    CreateCouponUseCase,
    DeleteCouponUseCase,
    ListCouponsUseCase,
    UpdateCouponUseCase,
    ValidateCouponUseCase,
)
from src.application.use_cases.products import (
    CreateProductUseCase,
    DeleteProductUseCase,
    GetProductUseCase,
    ListProductsUseCase,
    UpdateProductUseCase,
)
from src.application.use_cases.stores import (
    CreateStoreUseCase,
    DeleteStoreUseCase,
    GetStoreUseCase,
    ListStoresUseCase,
    UpdateStoreUseCase,
)

__all__ = [
    # Auth
    "RegisterUserUseCase",
    "LoginUserUseCase",
    "RefreshTokenUseCase",
    "GetCurrentUserUseCase",
    # Stores
    "CreateStoreUseCase",
    "GetStoreUseCase",
    "ListStoresUseCase",
    "UpdateStoreUseCase",
    "DeleteStoreUseCase",
    # Coupons
    "CreateCouponUseCase",
    "ValidateCouponUseCase",
    "ApplyCouponUseCase",
    "ListCouponsUseCase",
    "UpdateCouponUseCase",
    "DeleteCouponUseCase",
    # Products
    "CreateProductUseCase",
    "GetProductUseCase",
    "ListProductsUseCase",
    "UpdateProductUseCase",
    "DeleteProductUseCase",
]
