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
    "CreateStoreRequest",
    "StoreResponse",
    "UpdateStoreRequest",
]
