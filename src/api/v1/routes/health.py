"""Health check routes."""

from fastapi import APIRouter

from src.api.responses import SuccessResponse

router = APIRouter()


@router.get("/health", summary="Health check")
async def health_check():
    """Check if the API is running."""
    return SuccessResponse(
        data={"status": "healthy"},
        message="Service is running",
    )


@router.get("/", summary="Root endpoint")
async def root():
    """Root endpoint."""
    return SuccessResponse(
        data={
            "name": "NUMU API",
            "version": "1.0.0",
            "description": "Multi-tenant e-commerce platform API",
        },
        message="Welcome to NUMU API",
    )
