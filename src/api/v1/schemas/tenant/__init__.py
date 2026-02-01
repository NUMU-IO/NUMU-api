"""Tenant-scoped API schemas (products, stores, orders, invoices)."""

from src.api.v1.schemas.tenant.common import (
    DeleteResponse,
    MessageResponse,
    PaginatedListResponse,
    PaginationParams,
)
from src.api.v1.schemas.tenant.invoice import (
    CreateInvoiceRequest,
    InvoiceListResponse,
    InvoiceResponse,
    SubmitInvoiceResponse,
    UpdateInvoiceRequest,
)
from src.api.v1.schemas.tenant.product import (
    CreateProductRequest,
    ProductResponse,
    UpdateProductRequest,
)
from src.api.v1.schemas.tenant.store import (
    CheckSubdomainRequest,
    CheckSubdomainResponse,
    CreateStoreRequest,
    StoreResponse,
    UpdateStoreRequest,
)
from src.api.v1.schemas.tenant.order import (
    BulkUpdateOrderStatusRequest,
    BulkUpdateOrderStatusResponse,
    CreateOrderRequest,
    OrderAddressRequest,
    OrderAddressResponse,
    OrderDetailEnrichedResponse,
    OrderLineItemRequest,
    OrderLineItemResponse,
    OrderListItemResponse,
    OrderResponse,
    OrderTimelineEvent,
    OrderTimelineResponse,
    UpdateOrderRequest,
    UpdateOrderStatusRequest,
)

__all__ = [
    # Common
    "DeleteResponse",
    "MessageResponse",
    "PaginatedListResponse",
    "PaginationParams",
    # Invoice
    "CreateInvoiceRequest",
    "InvoiceListResponse",
    "InvoiceResponse",
    "SubmitInvoiceResponse",
    "UpdateInvoiceRequest",
    # Product
    "CreateProductRequest",
    "ProductResponse",
    "UpdateProductRequest",
    # Store
    "CheckSubdomainRequest",
    "CheckSubdomainResponse",
    "CreateStoreRequest",
    "StoreResponse",
    "UpdateStoreRequest",
    # Order
    "BulkUpdateOrderStatusRequest",
    "BulkUpdateOrderStatusResponse",
    "CreateOrderRequest",
    "OrderAddressRequest",
    "OrderAddressResponse",
    "OrderDetailEnrichedResponse",
    "OrderLineItemRequest",
    "OrderLineItemResponse",
    "OrderListItemResponse",
    "OrderResponse",
    "OrderTimelineEvent",
    "OrderTimelineResponse",
    "UpdateOrderRequest",
    "UpdateOrderStatusRequest",
]
