"""Core domain entities."""

from src.core.entities.address import AddressLabel, CustomerAddress
from src.core.entities.base import BaseEntity
from src.core.entities.capi_event import CapiEvent
from src.core.entities.cart import Cart
from src.core.entities.catalog_mapping import CatalogMapping, CatalogSyncStatus
from src.core.entities.category import Category
from src.core.entities.channel_connection import (
    ChannelConnection,
    ChannelType,
    ConnectionStatus,
)
from src.core.entities.channel_message import (
    ChannelMessage,
    MessageDirection,
    MessageStatus,
    MessageType,
)
from src.core.entities.coupon import Coupon, CouponType
from src.core.entities.customer import Customer
from src.core.entities.invoice import (
    BuyerInfo,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    InvoiceType,
    SellerInfo,
    TaxLine,
    TaxType,
)
from src.core.entities.message_log import (
    MessageLog,
)
from src.core.entities.message_thread import MessageThread, ThreadStatus
from src.core.entities.order import (
    VALID_STATUS_TRANSITIONS,
    FulfillmentStatus,
    Order,
    OrderLineItem,
    OrderStatus,
    PaymentStatus,
    ShippingAddress,
)
from src.core.entities.product import Product, ProductStatus, ProductType
from src.core.entities.store import Store, StoreStatus
from src.core.entities.theme import (
    StoreTheme,
    Theme,
    ThemeStatus,
    ThemeType,
    ThemeVersion,
)
from src.core.entities.user import User, UserRole, UserStatus
from src.core.entities.webhook_event import (
    WebhookEvent,
    WebhookProvider,
    WebhookStatus,
)
from src.core.entities.whatsapp_template import (
    TemplateCategory,
    TemplateStatus,
    WhatsAppTemplate,
)

__all__ = [
    # Base
    "BaseEntity",
    # Cart
    "Cart",
    # User
    "User",
    "UserRole",
    "UserStatus",
    # Store
    "Store",
    "StoreStatus",
    # Theme
    "Theme",
    "ThemeVersion",
    "StoreTheme",
    "ThemeType",
    "ThemeStatus",
    # Product
    "Product",
    "ProductStatus",
    "ProductType",
    # Category
    "Category",
    # Coupon
    "Coupon",
    "CouponType",
    # Customer
    "Customer",
    "CustomerAddress",
    "AddressLabel",
    # Order
    "FulfillmentStatus",
    "Order",
    "OrderLineItem",
    "OrderStatus",
    "PaymentStatus",
    "ShippingAddress",
    "VALID_STATUS_TRANSITIONS",
    # MessageLog
    "MessageLog",
    # Invoice
    "BuyerInfo",
    "Invoice",
    "InvoiceLineItem",
    "InvoiceStatus",
    "InvoiceType",
    "SellerInfo",
    "TaxLine",
    "TaxType",
    # Omnichannel
    "ChannelConnection",
    "ChannelType",
    "ConnectionStatus",
    "MessageThread",
    "ThreadStatus",
    "ChannelMessage",
    "MessageDirection",
    "MessageStatus",
    "MessageType",
    "WhatsAppTemplate",
    "TemplateCategory",
    "TemplateStatus",
    "CatalogMapping",
    "CatalogSyncStatus",
    "WebhookEvent",
    "WebhookProvider",
    "WebhookStatus",
    "CapiEvent",
]
