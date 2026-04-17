"""Omnichannel API routes."""

from .capi import router as capi_router
from .catalog import router as catalog_router
from .channels import router as channels_router
from .messages import router as messages_router
from .templates import router as templates_router
from .threads import router as threads_router

__all__ = [
    "channels_router",
    "threads_router",
    "messages_router",
    "templates_router",
    "catalog_router",
    "capi_router",
]
