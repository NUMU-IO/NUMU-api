"""Pydantic schemas for configuration request endpoints."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from src.infrastructure.database.models.tenant.configuration import (
    ServiceType,
    ServiceName,
    RequestStatus,
    RequestPriority,
)


class ConfigurationRequestCreate(BaseModel):
    """Schema for creating a new configuration request."""
    
    service_type: ServiceType = Field(
        ...,
        description="Type of service to configure (payment_gateway, shipping_carrier, etc.)"
    )
    service_name: ServiceName = Field(
        ...,
        description="Specific service provider name (fawry, paymob, aramex, etc.)"
    )
    notes: Optional[str] = Field(
        None,
        max_length=1000,
        description="Optional notes or requirements from the merchant"
    )
    priority: RequestPriority = Field(
        RequestPriority.NORMAL,
        description="Priority level for the request"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "service_type": "payment_gateway",
                "service_name": "fawry",
                "notes": "Please configure Fawry for our store. We need both card and reference payments.",
                "priority": "normal"
            }
        }


class ConfigurationRequestUpdate(BaseModel):
    """Schema for updating a configuration request (admin only)."""
    
    status: Optional[RequestStatus] = Field(
        None,
        description="New status for the request"
    )
    priority: Optional[RequestPriority] = Field(
        None,
        description="New priority level"
    )
    admin_notes: Optional[str] = Field(
        None,
        max_length=2000,
        description="Notes from the administrator"
    )
    assigned_to: Optional[UUID] = Field(
        None,
        description="Admin ID to assign the request to"
    )


class ConfigurationRequestResponse(BaseModel):
    """Schema for configuration request response."""
    
    id: UUID
    tenant_id: UUID
    requested_by: Optional[UUID]
    
    service_type: ServiceType
    service_name: ServiceName
    
    status: RequestStatus
    priority: RequestPriority
    
    merchant_notes: Optional[str]
    admin_notes: Optional[str]
    assigned_to: Optional[UUID]
    
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]
    
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
                "requested_by": "550e8400-e29b-41d4-a716-446655440002",
                "service_type": "payment_gateway",
                "service_name": "fawry",
                "status": "pending",
                "priority": "normal",
                "merchant_notes": "Please configure Fawry for our store.",
                "admin_notes": None,
                "assigned_to": None,
                "created_at": "2026-01-20T10:00:00Z",
                "updated_at": "2026-01-20T10:00:00Z",
                "completed_at": None
            }
        }


class ConfigurationRequestListResponse(BaseModel):
    """Schema for list of configuration requests."""
    
    items: list[ConfigurationRequestResponse]
    total: int
    page: int
    page_size: int
    
    class Config:
        json_schema_extra = {
            "example": {
                "items": [],
                "total": 0,
                "page": 1,
                "page_size": 20
            }
        }


class ConfigurationStatusResponse(BaseModel):
    """Schema for configuration status of a specific service."""
    
    service_type: ServiceType
    service_name: ServiceName
    
    is_configured: bool = Field(
        ...,
        description="Whether credentials are configured for this service"
    )
    is_active: bool = Field(
        ...,
        description="Whether the service is currently enabled"
    )
    is_validated: bool = Field(
        ...,
        description="Whether the credentials have been validated with the provider"
    )
    
    last_configured_at: Optional[datetime] = Field(
        None,
        description="When the credentials were last configured"
    )
    last_validated_at: Optional[datetime] = Field(
        None,
        description="When the credentials were last validated"
    )
    
    has_pending_request: bool = Field(
        ...,
        description="Whether there's a pending configuration request"
    )
    pending_request_id: Optional[UUID] = Field(
        None,
        description="ID of the pending request if any"
    )
    pending_request_status: Optional[RequestStatus] = Field(
        None,
        description="Status of the pending request"
    )
    
    # Display info (masked credentials)
    display_info: Optional[dict] = Field(
        None,
        description="Safe display information (masked values)"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "service_type": "payment_gateway",
                "service_name": "fawry",
                "is_configured": True,
                "is_active": True,
                "is_validated": True,
                "last_configured_at": "2026-01-15T10:00:00Z",
                "last_validated_at": "2026-01-15T10:05:00Z",
                "has_pending_request": False,
                "pending_request_id": None,
                "pending_request_status": None,
                "display_info": {
                    "merchant_code": "M***5678"
                }
            }
        }
