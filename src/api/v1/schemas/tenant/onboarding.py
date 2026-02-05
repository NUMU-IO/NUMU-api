"""Onboarding Pydantic schemas for API validation."""

from pydantic import BaseModel, Field


class OnboardingStepResponse(BaseModel):
    """Individual step in the onboarding response."""

    key: str = Field(description="Step identifier")
    status: str = Field(description="pending | completed | skipped")
    is_skippable: bool = Field(description="Whether this step can be skipped")
    completed_at: str | None = Field(
        default=None, description="ISO timestamp when completed"
    )
    skipped_at: str | None = Field(
        default=None, description="ISO timestamp when skipped"
    )


class OnboardingResponse(BaseModel):
    """Full onboarding state response."""

    id: str
    store_id: str
    steps: list[OnboardingStepResponse]
    completion_percentage: int = Field(ge=0, le=100)
    current_step: str | None = Field(
        description="Next pending step key, or null if all done"
    )
    is_completed: bool
    is_dismissed: bool
    completed_at: str | None = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True
