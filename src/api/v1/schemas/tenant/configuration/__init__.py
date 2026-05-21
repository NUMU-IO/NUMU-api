"""Pydantic schemas for configuration request API."""

from .credential_schemas import (
    CredentialConfigureRequest,
    CredentialStatusResponse,
    CredentialValidateRequest,
    ServiceInfoResponse,
    SupportedServicesResponse,
)
from .request_schemas import (
    ConfigurationRequestCreate,
    ConfigurationRequestListResponse,
    ConfigurationRequestResponse,
    ConfigurationRequestUpdate,
    ConfigurationStatusResponse,
)

__all__ = [
    # Request schemas
    "ConfigurationRequestCreate",
    "ConfigurationRequestUpdate",
    "ConfigurationRequestResponse",
    "ConfigurationRequestListResponse",
    "ConfigurationStatusResponse",
    # Credential schemas
    "CredentialConfigureRequest",
    "CredentialValidateRequest",
    "CredentialStatusResponse",
    "ServiceInfoResponse",
    "SupportedServicesResponse",
]
