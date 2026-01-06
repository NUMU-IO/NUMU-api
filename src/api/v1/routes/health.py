"""Health check routes."""

from fastapi import APIRouter

from src.api.responses import SuccessResponse

router = APIRouter(tags=["Health"])


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
            "name": "Octyrafiy API",
            "version": "1.0.0",
            "description": "E-commerce platform API",
        },
        message="Welcome to Octyrafiy API",
    )
