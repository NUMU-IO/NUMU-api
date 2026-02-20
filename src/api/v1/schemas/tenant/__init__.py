"""Tenant-scoped API schemas (products, stores, orders, invoices)."""

from src.api.v1.schemas.tenant.category import (
    CategoryResponse,
    CreateCategoryRequest,
    UpdateCategoryRequest,
)
from src.api.v1.schemas.tenant.common import (
    DeleteResponse,
    MessageResponse,
    PaginatedListResponse,
    PaginationParams,
)
from src.api.v1.schemas.tenant.coupon import (
    CouponResponse,
    CreateCouponRequest,
    UpdateCouponRequest,
)
from src.api.v1.schemas.tenant.invoice import (
    CreateInvoiceRequest,
    InvoiceListResponse,
    InvoiceResponse,
    SubmitInvoiceResponse,
    UpdateInvoiceRequest,
)
from src.api.v1.schemas.tenant.onboarding import (
    OnboardingResponse,
    OnboardingStepResponse,
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
from src.api.v1.schemas.tenant.product import (
    CreateProductRequest,
    DeleteImageRequest,
    ProductResponse,
    UpdateProductRequest,
    UploadedImageResponse,
)
from src.api.v1.schemas.tenant.product_import import (
    ImportResultResponse,
    ImportRowErrorResponse,
)
from src.api.v1.schemas.tenant.store import (
    CheckSubdomainRequest,
    CheckSubdomainResponse,
    CreateStoreRequest,
    StoreResponse,
    UpdateStoreRequest,
)

__all__ = [
    # Common
    "DeleteResponse",
    "MessageResponse",
    "PaginatedListResponse",
    "PaginationParams",
    # Category
    "CategoryResponse",
    "CreateCategoryRequest",
    "UpdateCategoryRequest",
    # Coupon
    "CouponResponse",
    "CreateCouponRequest",
    "UpdateCouponRequest",
    # Invoice
    "CreateInvoiceRequest",
    "InvoiceListResponse",
    "InvoiceResponse",
    "SubmitInvoiceResponse",
    "UpdateInvoiceRequest",
    # Product
    "CreateProductRequest",
    "DeleteImageRequest",
    "ProductResponse",
    "UpdateProductRequest",
    "UploadedImageResponse",
    # Product Import/Export
    "ImportResultResponse",
    "ImportRowErrorResponse",
    # Store
    "CheckSubdomainRequest",
    "CheckSubdomainResponse",
    "CreateStoreRequest",
    "StoreResponse",
    "UpdateStoreRequest",
    # Onboarding
    "OnboardingResponse",
    "OnboardingStepResponse",
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
