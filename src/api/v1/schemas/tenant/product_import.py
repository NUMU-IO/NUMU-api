"""Product import/export response schemas."""

from pydantic import BaseModel


class ImportRowErrorResponse(BaseModel):
    """Error detail for a single CSV row."""

    row: int
    field: str
    message: str


class ImportResultResponse(BaseModel):
    """Response for product CSV import."""

    total_rows: int
    created: int
    updated: int
    errors: list[ImportRowErrorResponse]
