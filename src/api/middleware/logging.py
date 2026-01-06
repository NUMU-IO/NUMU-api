"""Logging middleware."""

import logging
import time
import uuid
from typing import Callable

from fastapi import Request

logger = logging.getLogger(__name__)


async def logging_middleware(request: Request, call_next: Callable):
    """Log request and response details."""
    # Generate unique request ID
    request_id = str(uuid.uuid4())[:8]
    
    # Add request ID to state for access in route handlers
    request.state.request_id = request_id
    
    # Log request
    start_time = time.time()
    logger.info(
        f"[{request_id}] {request.method} {request.url.path} - Started"
    )
    
    # Process request
    response = await call_next(request)
    
    # Calculate processing time
    process_time = time.time() - start_time
    process_time_ms = round(process_time * 1000, 2)
    
    # Log response
    logger.info(
        f"[{request_id}] {request.method} {request.url.path} "
        f"- {response.status_code} - {process_time_ms}ms"
    )
    
    # Add headers
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(process_time_ms)
    
    return response
