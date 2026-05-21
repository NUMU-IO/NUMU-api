"""Repository interfaces."""

from src.core.interfaces.repositories.address_repository import (
    ICustomerAddressRepository,
)
from src.core.interfaces.repositories.base import BaseRepository
from src.core.interfaces.repositories.cart_repository import ICartRepository
from src.core.interfaces.repositories.catalog_mapping_repository import (
    CatalogMappingRepository,
)
from src.core.interfaces.repositories.category_repository import ICategoryRepository
from src.core.interfaces.repositories.channel_connection_repository import (
    ChannelConnectionRepository,
)
from src.core.interfaces.repositories.channel_message_repository import (
    ChannelMessageRepository,
)
from src.core.interfaces.repositories.coupon_repository import ICouponRepository
from src.core.interfaces.repositories.customer_repository import ICustomerRepository
from src.core.interfaces.repositories.message_log_repository import (
    IMessageLogRepository,
)
from src.core.interfaces.repositories.message_thread_repository import (
    MessageThreadRepository,
)
from src.core.interfaces.repositories.order_repository import IOrderRepository
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.repositories.tenant_repository import ITenantRepository
from src.core.interfaces.repositories.theme_repository import (
    IStoreThemeRepository,
    IThemeRepository,
    IThemeVersionRepository,
)
from src.core.interfaces.repositories.user_repository import IUserRepository
from src.core.interfaces.repositories.webhook_event_repository import (
    WebhookEventRepository,
)
from src.core.interfaces.repositories.whatsapp_template_repository import (
    WhatsAppTemplateRepository,
)

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
    "IThemeRepository",
    "IThemeVersionRepository",
    "IStoreThemeRepository",
    "IUserRepository",
    # Omnichannel
    "ChannelConnectionRepository",
    "MessageThreadRepository",
    "ChannelMessageRepository",
    "WhatsAppTemplateRepository",
    "CatalogMappingRepository",
    "WebhookEventRepository",
]
