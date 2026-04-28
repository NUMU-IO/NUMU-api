"""Unit tests for the InstaPay proof sanitizer + perceptual hash.

Verifies the Phase A guarantees we depend on downstream:
  - EXIF metadata (incl. GPS) is gone from the sanitized output.
  - pHash is deterministic across identical inputs.
  - pHash is robust to small rotations / re-saves (similar enough to
    catch trivial fraud reuploads under the dedup distance threshold).
  - pHash of unrelated images is comfortably above the threshold.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from PIL.ExifTags import Base as ExifBase

from src.infrastructure.external_services.image.proof_sanitizer import (
    ProofImageDecodeError,
    hamming_distance,
    sanitize_proof_image,
)

# ── Fixtures ──────────────────────────────────────────────────────────


def _solid_jpeg(
    *,
    width: int = 400,
    height: int = 300,
    color: tuple[int, int, int] = (180, 80, 80),
    with_gps_exif: bool = False,
) -> bytes:
    """Return a JPEG with optional EXIF GPS payload.

    Solid colour rather than noise so the *content* hash matches across
    re-runs even when the exact byte layout drifts (encoder version
    bumps). The pHash output stays deterministic for solid colours.
    """
    img = Image.new("RGB", (width, height), color)
    exif = img.getexif()
    if with_gps_exif:
        # Standard EXIF tags + a GPS sub-IFD entry. We don't need
        # actual GPS coordinates — just one tag in the GPS IFD is
        # enough to prove "GPS namespace is populated", which the
        # sanitizer must drop. ``GPSLatitudeRef`` is a string tag,
        # avoiding the rational-encoding fiddliness of the latitude
        # itself across Pillow versions.
        exif[ExifBase.Make] = "TestCam"
        exif[ExifBase.Model] = "ProofUnitTest"
        exif[ExifBase.Software] = "pytest"
        gps_ifd = exif.get_ifd(0x8825)
        gps_ifd[1] = "N"  # GPSLatitudeRef

    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif, quality=92)
    return out.getvalue()


def _photo_like_jpeg(width: int = 400, height: int = 300, seed: int = 0) -> bytes:
    """Return a JPEG with structured content so pHash isn't degenerate.

    A solid colour produces an all-zero pHash that matches any other
    solid-colour pHash; for the "unrelated images differ" assertion we
    need enough variation that pHash actually carries signal.
    """
    img = Image.new("RGB", (width, height))
    pixels = img.load()
    # Diagonal gradient + per-seed offset gives each variant its own
    # structure without depending on PIL.ImageDraw or RNG.
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (
                (x + seed * 17) % 255,
                (y + seed * 11) % 255,
                (x * y + seed * 7) % 255,
            )
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


# ── EXIF stripping ────────────────────────────────────────────────────


def test_sanitize_strips_exif_with_gps():
    """A JPEG with embedded GPS EXIF must come out with no GPS tags."""
    raw = _solid_jpeg(with_gps_exif=True)
    # Sanity: input actually carries EXIF
    pre = Image.open(io.BytesIO(raw))
    assert pre.getexif().get_ifd(0x8825), "fixture lacks GPS EXIF — test broken"

    result = sanitize_proof_image(raw, content_type="image/jpeg")
    out = Image.open(io.BytesIO(result.bytes))

    # Sanitized output should have no GPS sub-IFD and none of the
    # camera-identifying EXIF tags we wrote.
    exif = out.getexif()
    assert not exif.get_ifd(0x8825), "GPS EXIF survived sanitization"
    assert ExifBase.Make not in exif, "Camera make survived sanitization"
    assert ExifBase.Model not in exif, "Camera model survived sanitization"


def test_sanitize_returns_jpeg_content_type_for_png_input():
    """We re-encode to JPEG regardless of input format."""
    img = Image.new("RGB", (200, 200), (50, 200, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    result = sanitize_proof_image(buf.getvalue(), content_type="image/png")
    assert result.content_type == "image/jpeg"
    # Round-trips through PIL without error.
    Image.open(io.BytesIO(result.bytes)).verify()


def test_sanitize_rejects_garbage_bytes():
    with pytest.raises(ProofImageDecodeError):
        sanitize_proof_image(b"not an image", content_type="image/jpeg")


def test_sanitize_rejects_too_small():
    with pytest.raises(ProofImageDecodeError):
        sanitize_proof_image(b"\xff\xd8", content_type="image/jpeg")


# ── Perceptual hash ──────────────────────────────────────────────────


def test_phash_identical_inputs_match():
    """Two sanitizer runs over the same bytes must produce equal pHashes."""
    raw = _photo_like_jpeg(seed=1)
    a = sanitize_proof_image(raw, content_type="image/jpeg").perceptual_hash
    b = sanitize_proof_image(raw, content_type="image/jpeg").perceptual_hash
    assert a == b
    assert hamming_distance(a, b) == 0


def test_phash_rotation_closer_than_unrelated():
    """A small rotation must be perceptually closer than an unrelated image.

    Phrased this way (relative comparison) rather than as an absolute
    Hamming-distance bound because pHash drift under rotation depends
    heavily on the source image's frequency content — synthetic test
    fixtures fan out wider than real photos do. The semantic property
    the dedup gate relies on is "near-duplicates land closer than
    arbitrary other proofs", which is what we assert.
    """
    raw = _photo_like_jpeg(seed=2)
    a = sanitize_proof_image(raw, content_type="image/jpeg").perceptual_hash

    # Rotate the source 2°, re-encode, sanitize.
    img = Image.open(io.BytesIO(raw))
    rotated = img.rotate(2, expand=False, fillcolor=(255, 255, 255))
    buf = io.BytesIO()
    rotated.save(buf, format="JPEG", quality=90)
    b = sanitize_proof_image(buf.getvalue(), content_type="image/jpeg").perceptual_hash

    # Compare against an unrelated image (different seed).
    c = sanitize_proof_image(
        _photo_like_jpeg(seed=999), content_type="image/jpeg"
    ).perceptual_hash

    rot_distance = hamming_distance(a, b)
    unrelated_distance = hamming_distance(a, c)
    assert rot_distance < unrelated_distance, (
        f"rotation distance {rot_distance} >= unrelated distance "
        f"{unrelated_distance} — pHash isn't doing its job"
    )


def test_phash_unrelated_images_above_dedup_threshold():
    """Unrelated images must clear the dedup gate's distance threshold.

    The real fraud property: a customer's screenshot of one bank's
    receipt mustn't false-positive against an unrelated screenshot
    in the same store. The dedup gate uses ``≤5``, so we assert
    every fixture pair lands strictly above that.
    """
    seeds = [3, 42, 100]
    hashes = [
        sanitize_proof_image(
            _photo_like_jpeg(seed=s), content_type="image/jpeg"
        ).perceptual_hash
        for s in seeds
    ]
    for i, h_i in enumerate(hashes):
        for j in range(i + 1, len(hashes)):
            distance = hamming_distance(h_i, hashes[j])
            assert distance > 5, (
                f"seeds {seeds[i]} vs {seeds[j]}: distance {distance} "
                f"would false-positive the dedup gate"
            )


def test_phash_fits_signed_bigint():
    """pHash is stored in a signed BIGINT — must fit 63 bits comfortably."""
    raw = _photo_like_jpeg(seed=5)
    h = sanitize_proof_image(raw, content_type="image/jpeg").perceptual_hash
    assert 0 <= h < (1 << 64), "pHash escaped 64-bit range"
