"""Repository interfaces."""

from src.core.interfaces.repositories.address_repository import (
    ICustomerAddressRepository,
)
from src.core.interfaces.repositories.base import BaseRepository
from src.core.interfaces.repositories.cart_repository import ICartRepository
from src.core.interfaces.repositories.category_repository import ICategoryRepository
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.repositories.message_log_repository import (
    IMessageLogRepository,
)
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.repositories.tenant_repository import ITenantRepository
from src.core.interfaces.repositories.user_repository import IUserRepository

__all__ = [
    "BaseRepository",
    "ICartRepository",
    "ICategoryRepository",
    "ICouponRepository",
    "ICustomerRepository",
    "ICustomerAddressRepository",
    "IMessageLogRepository",
    "IOrderRepository",
    "IProductRepository",
    "IStoreRepository",
    "ITenantRepository",
    "IUserRepository",
]
