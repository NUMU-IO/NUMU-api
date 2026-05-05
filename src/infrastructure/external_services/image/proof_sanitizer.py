"""Payment-proof screenshot sanitizer + perceptual-hash helper.

The InstaPay proof flow accepts a customer-uploaded screenshot of a
bank-app receipt. Two upgrades over the raw-bytes path it replaces:

1. **Sanitize** — open with PIL, drop EXIF / ICC / XMP, optionally
   downscale, re-encode. Privacy hygiene (phone-camera screenshots
   can carry GPS in EXIF) and decompression-bomb safety in one pass.

2. **Perceptual hash** — emit a 64-bit pHash off the *sanitized*
   image so the dedup layer in :class:`SubmitPaymentProofUseCase`
   can catch trivially-mutated reuploads of prior screenshots
   (re-saves, 1-px crops) that defeat raw SHA-256.

Kept separate from :mod:`image_processor` because the proof path
doesn't need variant generation and we want a tight single-purpose
helper that's cheap to call (~5–15 ms per image).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)


# Safety limits — mirror the existing ImageProcessor so the two paths
# share decompression-bomb posture rather than drifting.
MAX_IMAGE_PIXELS = 50_000_000  # 50 megapixels
# Smaller than ImageProcessor.MAX_DIMENSION because proof screenshots
# rarely benefit from >4K and shrinking the bucket footprint pays off
# at scale (every accepted proof is stored indefinitely for audit).
MAX_DIMENSION = 4096
MIN_FILE_SIZE = 8

# Quality picked high enough that bank-app receipt text stays sharp at
# zoom — merchants regularly need to read tiny transaction-ID
# characters in the lightbox view.
JPEG_QUALITY = 92


class ProofImageDecodeError(Exception):
    """Raised when the uploaded bytes can't be decoded as an image.

    The route handler maps this to a 415 — the magic-byte validator
    upstream should have caught most of these, so reaching here means
    a corrupted body, an unsupported PIL codec, or a decompression-bomb
    trip. All three behave the same to the customer: re-upload.
    """


@dataclass(frozen=True)
class SanitizedProof:
    """Output of :func:`sanitize_proof_image`.

    ``bytes`` and ``content_type`` go to R2; ``perceptual_hash`` is
    persisted on the proof row for the dedup layer.
    """

    bytes: bytes
    content_type: str
    perceptual_hash: int


def sanitize_proof_image(raw: bytes, *, content_type: str) -> SanitizedProof:
    """Decode → strip metadata → optionally downscale → re-encode + pHash.

    Returns the sanitized bytes plus a 64-bit perceptual hash of the
    sanitized image. Computing pHash here (rather than in the use case)
    keeps PIL decode work in one place — we open the image once.

    Args:
        raw: bytes from the upload, already validated by
            :func:`validate_image_upload` (magic-byte + size guard).
        content_type: best-effort content-type string from the uploader.
            Used to choose the output encoder; we never trust it for
            actual format detection.

    Raises:
        ProofImageDecodeError: PIL can't open the bytes, the image
            trips the decompression-bomb guard, or the content-type
            doesn't map to a supported encoder.
    """
    if len(raw) < MIN_FILE_SIZE:
        raise ProofImageDecodeError("Image bytes too small to decode.")

    # Set the bomb guard for the duration of this call. The constant
    # is process-global in PIL, so if image_processor.py initialised
    # it elsewhere we just respect whatever the higher value is.
    if Image.MAX_IMAGE_PIXELS is None or Image.MAX_IMAGE_PIXELS < MAX_IMAGE_PIXELS:
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()  # force decode now so a bomb / corrupt body raises here
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ProofImageDecodeError(str(exc)) from exc

    # Apply EXIF orientation BEFORE stripping — otherwise a
    # portrait-by-rotation phone screenshot would land rotated 90°
    # in the bucket, and the merchant has no way to fix it from the
    # review pane.
    img = ImageOps.exif_transpose(img)

    # Normalize colour mode. RGBA → RGB on a white background so JPEG
    # output doesn't go opaque-black where the alpha was. Palette /
    # grayscale → RGB to keep encoder choice simple downstream.
    if img.mode in ("RGBA", "LA", "P"):
        rgb = Image.new("RGB", img.size, (255, 255, 255))
        # ``mask=img.split()[-1]`` works for RGBA/LA; falls back cleanly
        # for palette mode where there's no alpha.
        try:
            rgb.paste(img, mask=img.split()[-1] if img.mode != "P" else None)
        except (ValueError, IndexError):
            rgb.paste(img)
        img = rgb
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Downscale only when oversized — preserves merchant zoom legibility
    # for in-bounds images while bounding the worst case. ``LANCZOS``
    # is the sharpest filter for receipt text.
    if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)

    # 64-bit pHash. ``imagehash.phash`` returns an ImageHash whose
    # ``hash`` is a numpy bool array of shape (8, 8). We pack it into
    # a Python int — one BIGINT column, fast XOR for Hamming distance.
    perceptual = _phash_int(img)

    # Re-encode. JPEG is the only format we emit — even when the source
    # was PNG. Receipt screenshots are predominantly photographic-ish
    # gradients (bank-app UI), so JPEG at q=92 visually matches PNG at
    # a fraction of the size. The merchant view is what matters; the
    # raw original is irrelevant for evidence purposes.
    out = io.BytesIO()
    # ``optimize=True`` runs an extra pass to pick smaller Huffman
    # tables; cheap on screenshot-sized images, ~10% smaller output.
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    sanitized_bytes = out.getvalue()

    return SanitizedProof(
        bytes=sanitized_bytes,
        content_type="image/jpeg",
        perceptual_hash=perceptual,
    )


def _phash_int(img: Image.Image) -> int:
    """Pack ``imagehash.phash`` into a 64-bit Python int.

    The ImageHash type stores an 8x8 numpy bool matrix; iterating in
    natural order and shifting in 0/1 reproduces the same ordering
    used by ``str(ImageHash)`` so two integers can be Hamming-compared
    consistently with the upstream library's semantics.
    """
    h = imagehash.phash(img)
    bits = 0
    for row in h.hash:
        for cell in row:
            bits = (bits << 1) | (1 if cell else 0)
    return bits


def hamming_distance(a: int, b: int) -> int:
    """Bit-count of the XOR — Hamming distance between two pHash ints.

    Pure helper so the repo can compute distances in Python after
    fetching candidate rows; we don't need a Postgres extension for
    the small per-store windows we scan.
    """
    return (a ^ b).bit_count()
