"""Health check routes with comprehensive system status."""

import shutil
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import redis.asyncio as redis
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.config import settings
from src.config.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


class HealthStatus(str, Enum):
    """Health status values."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth(BaseModel):
    """Health status of a single component."""

    status: HealthStatus
    latency_ms: float | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


class DetailedHealthResponse(BaseModel):
    """Detailed health check response."""

    status: HealthStatus
    timestamp: str
    version: str
    environment: str
    components: dict[str, ComponentHealth]
    system: dict[str, Any]


async def check_database(db: AsyncSession) -> ComponentHealth:
    """Check database connectivity."""
    start = datetime.now(UTC)
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        latency = (datetime.now(UTC) - start).total_seconds() * 1000
        return ComponentHealth(
            status=HealthStatus.HEALTHY,
            latency_ms=round(latency, 2),
            message="Database connection successful",
        )
    except Exception as e:
        latency = (datetime.now(UTC) - start).total_seconds() * 1000
        logger.error("health_check_db_failed", error=str(e))
        return ComponentHealth(
            status=HealthStatus.UNHEALTHY,
            latency_ms=round(latency, 2),
            message=f"Database connection failed: {type(e).__name__}",
        )


async def check_redis() -> ComponentHealth:
    """Check Redis connectivity."""
    start = datetime.now(UTC)
    try:
        client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await client.ping()
        info = await client.info("server")
        await client.close()
        latency = (datetime.now(UTC) - start).total_seconds() * 1000
        return ComponentHealth(
            status=HealthStatus.HEALTHY,
            latency_ms=round(latency, 2),
            message="Redis connection successful",
            details={
                "redis_version": info.get("redis_version"),
                "uptime_seconds": info.get("uptime_in_seconds"),
            },
        )
    except Exception as e:
        latency = (datetime.now(UTC) - start).total_seconds() * 1000
        logger.error("health_check_redis_failed", error=str(e))
        return ComponentHealth(
            status=HealthStatus.UNHEALTHY,
            latency_ms=round(latency, 2),
            message=f"Redis connection failed: {type(e).__name__}",
        )


def check_sentry() -> ComponentHealth:
    """Check Sentry configuration status."""
    if settings.sentry_dsn:
        return ComponentHealth(
            status=HealthStatus.HEALTHY,
            message="Sentry DSN configured",
            details={
                "environment": settings.environment,
                "traces_sample_rate": settings.sentry_traces_sample_rate,
            },
        )
    return ComponentHealth(
        status=HealthStatus.DEGRADED,
        message="Sentry DSN not configured - error tracking disabled",
    )


def check_disk_space() -> ComponentHealth:
    """Check available disk space."""
    try:
        total, used, free = shutil.disk_usage("/")
        free_gb = free / (1024**3)
        total_gb = total / (1024**3)
        used_percent = (used / total) * 100

        # Warning if less than 10% or 1GB free
        if free_gb < 1 or used_percent > 90:
            status = HealthStatus.DEGRADED
            message = f"Low disk space: {free_gb:.1f}GB free ({100 - used_percent:.1f}% available)"
        else:
            status = HealthStatus.HEALTHY
            message = f"Disk space OK: {free_gb:.1f}GB free"

        return ComponentHealth(
            status=status,
            message=message,
            details={
                "total_gb": round(total_gb, 2),
                "free_gb": round(free_gb, 2),
                "used_percent": round(used_percent, 1),
            },
        )
    except Exception as e:
        logger.error("health_check_disk_failed", error=str(e))
        return ComponentHealth(
            status=HealthStatus.DEGRADED,
            message=f"Could not check disk space: {type(e).__name__}",
        )


def determine_overall_status(components: dict[str, ComponentHealth]) -> HealthStatus:
    """Determine overall health status from component statuses."""
    statuses = [c.status for c in components.values()]

    if any(s == HealthStatus.UNHEALTHY for s in statuses):
        return HealthStatus.UNHEALTHY
    if any(s == HealthStatus.DEGRADED for s in statuses):
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY


@router.get("/health", summary="Basic health check")
async def health_check():
    """Basic health check - returns healthy if API is running.

    Use /health/detailed for comprehensive system status.
    """
    return SuccessResponse(
        data={"status": "healthy"},
        message="Service is running",
    )


@router.get(
    "/health/detailed",
    summary="Detailed health check",
    response_model=DetailedHealthResponse,
)
async def detailed_health_check(
    db: AsyncSession = Depends(get_db),
) -> DetailedHealthResponse:
    """Comprehensive health check with component status.

    Checks:
    - Database connectivity and latency
    - Redis connectivity and latency
    - Sentry configuration status
    - Disk space availability

    Returns detailed status for monitoring and alerting.
    """
    # Run health checks
    db_health = await check_database(db)
    redis_health = await check_redis()
    sentry_health = check_sentry()
    disk_health = check_disk_space()

    components = {
        "database": db_health,
        "redis": redis_health,
        "sentry": sentry_health,
        "disk": disk_health,
    }

    overall_status = determine_overall_status(components)

    # Log health check result
    logger.info(
        "health_check_completed",
        status=overall_status.value,
        db_status=db_health.status.value,
        redis_status=redis_health.status.value,
    )

    return DetailedHealthResponse(
        status=overall_status,
        timestamp=datetime.now(UTC).isoformat(),
        version=settings.app_version,
        environment=settings.environment,
        components=components,
        system={
            "debug_mode": settings.debug,
            "api_prefix": settings.api_v1_prefix,
        },
    )


@router.get("/", summary="Root endpoint")
async def root():
    """Root endpoint with API information."""
    return SuccessResponse(
        data={
            "name": "NUMU API",
            "version": settings.app_version,
            "description": "Multi-tenant e-commerce platform API",
            "environment": settings.environment,
        },
        message="Welcome to NUMU API",
    )
