"""Repository interfaces."""

from src.core.interfaces.repositories.base import BaseRepository
from src.core.interfaces.repositories.category_repository import ICategoryRepository
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.repositories.user_repository import IUserRepository

__all__ = [
    "BaseRepository",
    "IUserRepository",
    "IStoreRepository",
    "IProductRepository",
    "ICustomerRepository",
    "IOrderRepository",
    "ICategoryRepository",
]
