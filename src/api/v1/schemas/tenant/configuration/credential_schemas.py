"""Pydantic schemas for credential configuration endpoints (admin only)."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from src.infrastructure.database.models.tenant.configuration import (
    ServiceType,
    ServiceName,
)


class CredentialConfigureRequest(BaseModel):
    """Schema for configuring credentials (admin only).
    
    This is used by administrators to set up service credentials
    for a merchant's store.
    """
    
    tenant_id: UUID = Field(
        ...,
        description="Tenant/merchant ID to configure credentials for"
    )
    service_type: ServiceType = Field(
        ...,
        description="Type of service"
    )
    service_name: ServiceName = Field(
        ...,
        description="Specific service provider"
    )
    credentials: dict[str, Any] = Field(
        ...,
        description="Service credentials (will be encrypted)"
    )
    request_id: Optional[UUID] = Field(
        None,
        description="Configuration request ID if responding to a request"
    )
    admin_notes: Optional[str] = Field(
        None,
        max_length=2000,
        description="Notes about the configuration"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
                "service_type": "payment_gateway",
                "service_name": "fawry",
                "credentials": {
                    "merchant_code": "M12345678",
                    "security_key": "your-security-key-here",
                    "environment": "production"
                },
                "request_id": "550e8400-e29b-41d4-a716-446655440003",
                "admin_notes": "Configured with production credentials"
            }
        }


class CredentialValidateRequest(BaseModel):
    """Schema for validating credentials without storing them."""
    
    service_type: ServiceType = Field(
        ...,
        description="Type of service"
    )
    service_name: ServiceName = Field(
        ...,
        description="Specific service provider"
    )
    credentials: dict[str, Any] = Field(
        ...,
        description="Credentials to validate"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "service_type": "payment_gateway",
                "service_name": "paymob",
                "credentials": {
                    "api_key": "your-api-key",
                    "integration_id": "12345"
                }
            }
        }


class CredentialValidationResponse(BaseModel):
    """Schema for credential validation response."""
    
    is_valid: bool
    status: str
    message: str
    details: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "is_valid": True,
                "status": "valid",
                "message": "Credentials validated successfully",
                "details": {
                    "merchant_code": "M12345678",
                    "environment": "production"
                },
                "error_code": None
            }
        }


class CredentialStatusResponse(BaseModel):
    """Schema for credential status response."""
    
    tenant_id: UUID
    service_type: ServiceType
    service_name: ServiceName
    
    is_configured: bool
    is_active: bool
    is_validated: bool
    
    configured_at: Optional[datetime]
    configured_by: Optional[UUID]
    last_validated_at: Optional[datetime]
    
    # Masked display info
    display_info: Optional[dict[str, str]] = None
    
    class Config:
        from_attributes = True


class ServiceInfoResponse(BaseModel):
    """Schema for service information."""
    
    service_type: ServiceType
    service_name: ServiceName
    display_name: str
    description: str
    required_fields: list[str]
    optional_fields: list[str]
    documentation_url: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "service_type": "payment_gateway",
                "service_name": "fawry",
                "display_name": "Fawry",
                "description": "Egypt's leading payment network",
                "required_fields": ["merchant_code", "security_key"],
                "optional_fields": ["return_url", "environment"],
                "documentation_url": "https://developer.fawry.io"
            }
        }


class SupportedServicesResponse(BaseModel):
    """Schema for list of supported services."""
    
    payment_gateways: list[ServiceInfoResponse]
    shipping_carriers: list[ServiceInfoResponse]
    communication: list[ServiceInfoResponse]
    
    class Config:
        json_schema_extra = {
            "example": {
                "payment_gateways": [],
                "shipping_carriers": [],
                "communication": []
            }
        }
