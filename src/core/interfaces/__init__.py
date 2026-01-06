"""Core interfaces module."""

from src.core.interfaces.repositories import (
    BaseRepository,
    ICategoryRepository,
    ICustomerRepository,
    IOrderRepository,
    IProductRepository,
    IStoreRepository,
    IUserRepository,
)
from src.core.interfaces.services import (
    IAIService,
    ICacheService,
    IEmailService,
    IPasswordService,
    IPaymentService,
    IShippingService,
    IStorageService,
    ITokenService,
)

__all__ = [
    # Repositories
    "BaseRepository",
    "IUserRepository",
    "IStoreRepository",
    "IProductRepository",
    "ICustomerRepository",
    "IOrderRepository",
    "ICategoryRepository",
    # Services
    "IPasswordService",
    "ITokenService",
    "IEmailService",
    "IPaymentService",
    "IStorageService",
    "IShippingService",
    "IAIService",
    "ICacheService",
]
