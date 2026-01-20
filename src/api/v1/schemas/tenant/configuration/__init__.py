"""Pydantic schemas for configuration request API."""

from .request_schemas import (
    ConfigurationRequestCreate,
    ConfigurationRequestUpdate,
    ConfigurationRequestResponse,
    ConfigurationRequestListResponse,
    ConfigurationStatusResponse,
)
from .credential_schemas import (
    CredentialConfigureRequest,
    CredentialValidateRequest,
    CredentialStatusResponse,
    ServiceInfoResponse,
    SupportedServicesResponse,
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
