"""File upload validation utilities.

Provides route-level validation for uploaded files:
  - Size limits (defense in depth — use cases also validate)
  - MIME type validation via magic bytes (not just extension/Content-Type header)
  - CSV content-type verification

These checks run *before* the request body is passed to use cases,
rejecting obviously invalid uploads early with clear HTTP 413/415 errors.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, UploadFile, status

logger = logging.getLogger(__name__)

# ── Size limits ──────────────────────────────────────────────

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_CSV_SIZE = 10 * 1024 * 1024  # 10 MB

# ── Magic byte signatures ────────────────────────────────────
# https://en.wikipedia.org/wiki/List_of_file_signatures

_IMAGE_MAGIC: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),  # JPEG
    (b"\x89PNG\r\n\x1a\n", "image/png"),  # PNG
    (b"RIFF", "image/webp"),  # WebP (RIFF container)
    (b"GIF87a", "image/gif"),  # GIF87a
    (b"GIF89a", "image/gif"),  # GIF89a
    (b"BM", "image/bmp"),  # BMP
    (b"II\x2a\x00", "image/tiff"),  # TIFF little-endian
    (b"MM\x00\x2a", "image/tiff"),  # TIFF big-endian
]

_ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_ALLOWED_CSV_MIMES = {
    "text/csv",
    "application/csv",
    "text/plain",
    "application/vnd.ms-excel",
    "application/octet-stream",
}


def _detect_image_mime(header: bytes) -> str | None:
    """Detect image MIME type from magic bytes.

    Returns the MIME type string or ``None`` if no signature matches.
    """
    for magic, mime in _IMAGE_MAGIC:
        if header[: len(magic)] == magic:
            # WebP requires additional check: bytes 8-12 should be "WEBP"
            if mime == "image/webp" and header[8:12] != b"WEBP":
                continue
            return mime
    return None


async def validate_image_upload(file: UploadFile) -> bytes:
    """Validate an image upload and return the file bytes.

    Checks:
      1. File size ≤ 5 MB (HTTP 413 if exceeded)
      2. Magic bytes match a supported image format (HTTP 415 if not)

    Returns the full file content as ``bytes`` so callers don't need to
    re-read the stream.

    Raises:
        HTTPException 413: File too large.
        HTTPException 415: Unsupported media type (magic bytes mismatch).
    """
    content = await file.read()

    if len(content) > MAX_IMAGE_SIZE:
        size_mb = len(content) / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Image file size ({size_mb:.1f} MB) exceeds the "
                f"{MAX_IMAGE_SIZE // (1024 * 1024)} MB limit."
            ),
        )

    if len(content) < 8:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File is too small to be a valid image.",
        )

    detected = _detect_image_mime(content[:16])
    if detected is None or detected not in _ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=("Unsupported image format. Allowed formats: JPEG, PNG, WebP, GIF."),
        )

    return content


async def validate_csv_upload(file: UploadFile) -> bytes:
    """Validate a CSV upload and return the file bytes.

    Checks:
      1. Content-Type header is a CSV-compatible MIME type (HTTP 415)
      2. File size ≤ 10 MB (HTTP 413)

    Returns the full file content as ``bytes``.
    """
    ct = (file.content_type or "").lower()
    if ct and ct not in _ALLOWED_CSV_MIMES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Expected a CSV file, got '{ct}'.",
        )

    content = await file.read()

    if len(content) > MAX_CSV_SIZE:
        size_mb = len(content) / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"CSV file size ({size_mb:.1f} MB) exceeds the "
                f"{MAX_CSV_SIZE // (1024 * 1024)} MB limit."
            ),
        )

    return content
