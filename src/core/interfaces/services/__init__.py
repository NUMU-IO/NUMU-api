"""Service interfaces."""

from src.core.interfaces.services.ai_service import (
    ChatMessage,
    ChatResponse,
    IAIService,
    ProductDescription,
)
from src.core.interfaces.services.audit_service import (
    AuditEvent,
    AuditEventSeverity,
    AuditEventType,
    AuditLogEntry,
    IAuditService,
)
from src.core.interfaces.services.cache_service import ICacheService
from src.core.interfaces.services.email_service import EmailMessage, IEmailService
from src.core.interfaces.services.messaging_service import (
    EGYPTIAN_TEMPLATES,
    IMessagingService,
    MessageChannel,
    MessageContent,
    MessageRecipient,
    MessageResult,
    MessageStatus,
    MessageTemplate,
    MessageType,
)
from src.core.interfaces.services.password_service import IPasswordService
from src.core.interfaces.services.payment_service import (
    IPaymentService,
    PaymentIntent,
    PaymentProvider,
    PaymentResult,
    RefundResult,
)
from src.core.interfaces.services.shipping_service import (
    IShippingService,
    Parcel,
    ShipmentLabel,
    ShippingAddress,
    ShippingRate,
    TrackingEvent,
    TrackingInfo,
)
from src.core.interfaces.services.storage_service import (
    IStorageService,
    StorageBucket,
    UploadedFile,
)
from src.core.interfaces.services.token_service import ITokenService, TokenPayload

__all__ = [
    # Audit
    "AuditEvent",
    "AuditEventSeverity",
    "AuditEventType",
    "AuditLogEntry",
    "IAuditService",
    # AI
    "ChatMessage",
    "ChatResponse",
    "IAIService",
    "ProductDescription",
    # Cache
    "ICacheService",
    # Email
    "EmailMessage",
    "IEmailService",
    # Messaging
    "EGYPTIAN_TEMPLATES",
    "IMessagingService",
    "MessageChannel",
    "MessageContent",
    "MessageRecipient",
    "MessageResult",
    "MessageStatus",
    "MessageTemplate",
    "MessageType",
    # Password
    "IPasswordService",
    # Payment
    "IPaymentService",
    "PaymentIntent",
    "PaymentProvider",
    "PaymentResult",
    "RefundResult",
    # Shipping
    "IShippingService",
    "Parcel",
    "ShipmentLabel",
    "ShippingAddress",
    "ShippingRate",
    "TrackingEvent",
    "TrackingInfo",
    # Storage
    "IStorageService",
    "StorageBucket",
    "UploadedFile",
    # Token
    "ITokenService",
    "TokenPayload",
]
