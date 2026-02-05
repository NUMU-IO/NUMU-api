"""Image processing service using Pillow.

Provides image resizing, compression, WebP conversion, and EXIF stripping
for product image optimization.
"""

import io
import logging
from dataclasses import dataclass
from enum import StrEnum

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Safety limits
MAX_IMAGE_PIXELS = 50_000_000  # 50 megapixels
MAX_DIMENSION = 8192  # Max width or height in pixels
MIN_FILE_SIZE = 8  # Minimum bytes for a valid image


class ImageVariantName(StrEnum):
    """Standard image variant names."""

    ORIGINAL = "original"
    LARGE = "large"
    MEDIUM = "medium"
    THUMBNAIL = "thumbnail"


@dataclass(frozen=True)
class ImageVariantConfig:
    """Configuration for an image size variant."""

    name: ImageVariantName
    max_width: int
    max_height: int
    quality: int


# Standard variant configurations per requirements:
# thumbnail 150px, medium 600px, large 1200px, quality 85%
DEFAULT_VARIANTS = [
    ImageVariantConfig(
        name=ImageVariantName.THUMBNAIL,
        max_width=150,
        max_height=150,
        quality=80,
    ),
    ImageVariantConfig(
        name=ImageVariantName.MEDIUM,
        max_width=600,
        max_height=600,
        quality=85,
    ),
    ImageVariantConfig(
        name=ImageVariantName.LARGE,
        max_width=1200,
        max_height=1200,
        quality=85,
    ),
]


@dataclass
class ProcessedImageResult:
    """Result of processing a single image variant."""

    variant_name: str
    data: bytes
    width: int
    height: int
    content_type: str
    file_size: int


class ImageProcessingError(Exception):
    """Raised when image processing fails."""


class ImageProcessor:
    """Service for processing product images.

    Handles:
    - Image validation (format, dimensions, decompression bomb protection)
    - EXIF metadata stripping (with orientation preservation)
    - Resizing to multiple variants (thumbnail, medium, large)
    - WebP conversion with configurable quality
    - ICC color profile preservation
    """

    SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"}

    def __init__(
        self,
        variants: list[ImageVariantConfig] | None = None,
        output_quality: int = 85,
    ) -> None:
        self.variants = variants or DEFAULT_VARIANTS
        self.output_quality = output_quality
        # Set Pillow's decompression bomb limit
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

    def validate(self, file_data: bytes) -> Image.Image:
        """Validate and open an image from raw bytes.

        Args:
            file_data: Raw image file bytes.

        Returns:
            Opened and validated PIL Image.

        Raises:
            ImageProcessingError: If validation fails.
        """
        if len(file_data) < MIN_FILE_SIZE:
            raise ImageProcessingError("File too small to be a valid image")

        try:
            img = Image.open(io.BytesIO(file_data))
        except UnidentifiedImageError:
            raise ImageProcessingError(
                "Cannot identify image format. File may be corrupt or unsupported."
            )
        except (OSError, SyntaxError) as exc:
            raise ImageProcessingError(f"Failed to open image: {exc}")

        if img.format not in self.SUPPORTED_FORMATS:
            raise ImageProcessingError(
                f"Unsupported image format '{img.format}'. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_FORMATS))}"
            )

        width, height = img.size
        if width <= 0 or height <= 0:
            raise ImageProcessingError(f"Invalid image dimensions: {width}x{height}")

        if width > MAX_DIMENSION or height > MAX_DIMENSION:
            raise ImageProcessingError(
                f"Image dimensions {width}x{height} exceed "
                f"maximum {MAX_DIMENSION}x{MAX_DIMENSION}"
            )

        # Force-load pixel data to catch truncation/corruption
        try:
            img.load()
        except (OSError, Image.DecompressionBombError) as exc:
            raise ImageProcessingError(f"Image data is corrupt or too large: {exc}")

        # For animated images (GIF/WebP), use first frame only
        if hasattr(img, "n_frames") and img.n_frames > 1:
            img.seek(0)
            logger.info(
                "Animated image detected (%d frames), using first frame",
                img.n_frames,
            )

        return img

    def strip_exif(self, img: Image.Image) -> Image.Image:
        """Strip EXIF metadata while preserving orientation and ICC profile.

        Applies EXIF orientation transform before stripping so images
        taken in portrait mode are not displayed sideways.

        Args:
            img: PIL Image to strip metadata from.

        Returns:
            New image with EXIF removed.
        """
        # Apply EXIF orientation before stripping
        img = ImageOps.exif_transpose(img)
        return img

    def normalize_mode(self, img: Image.Image) -> Image.Image:
        """Normalize image color mode for WebP output.

        Args:
            img: PIL Image to normalize.

        Returns:
            Image with normalized color mode (RGB or RGBA).
        """
        if img.mode == "P":
            # Palette mode - check for transparency
            if "transparency" in img.info:
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
        elif img.mode == "CMYK":
            img = img.convert("RGB")
        elif img.mode == "LA":
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        return img

    def resize(
        self,
        img: Image.Image,
        max_width: int,
        max_height: int,
    ) -> Image.Image:
        """Resize image to fit within max dimensions, maintaining aspect ratio.

        Uses LANCZOS resampling for highest quality downscaling.
        Never upscales - returns a copy if image is already smaller.

        Args:
            img: PIL Image to resize.
            max_width: Maximum width in pixels.
            max_height: Maximum height in pixels.

        Returns:
            Resized image (or copy of original if already small enough).
        """
        thumb = img.copy()
        thumb.thumbnail(
            (max_width, max_height),
            resample=Image.Resampling.LANCZOS,
            reducing_gap=2.0,
        )
        return thumb

    def convert_to_webp(
        self,
        img: Image.Image,
        quality: int = 85,
    ) -> bytes:
        """Convert image to WebP format with compression.

        Preserves ICC color profile if present. Does not write EXIF
        (since it was stripped earlier in the pipeline).

        Args:
            img: PIL Image to convert.
            quality: WebP quality (0-100). Default 85.

        Returns:
            WebP-encoded image bytes.
        """
        buffer = io.BytesIO()

        save_kwargs: dict = {
            "format": "WEBP",
            "quality": quality,
            "method": 4,  # Good balance of speed vs compression
        }

        # Preserve ICC profile for color accuracy
        icc_profile = img.info.get("icc_profile")
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile

        # Handle alpha channel quality
        if img.mode == "RGBA":
            save_kwargs["alpha_quality"] = 85

        img.save(buffer, **save_kwargs)
        return buffer.getvalue()

    def process_variant(
        self,
        img: Image.Image,
        config: ImageVariantConfig,
    ) -> ProcessedImageResult:
        """Process a single image variant (resize + compress to WebP).

        Args:
            img: Source PIL Image (already stripped of EXIF and normalized).
            config: Variant configuration (name, dimensions, quality).

        Returns:
            ProcessedImageResult with variant data and metadata.
        """
        resized = self.resize(img, config.max_width, config.max_height)
        webp_data = self.convert_to_webp(resized, quality=config.quality)

        return ProcessedImageResult(
            variant_name=config.name.value,
            data=webp_data,
            width=resized.size[0],
            height=resized.size[1],
            content_type="image/webp",
            file_size=len(webp_data),
        )

    def optimize_original(
        self,
        img: Image.Image,
        quality: int | None = None,
    ) -> ProcessedImageResult:
        """Optimize the original image (convert to WebP without resizing).

        Args:
            img: Source PIL Image (already stripped of EXIF and normalized).
            quality: WebP quality. Defaults to self.output_quality.

        Returns:
            ProcessedImageResult for the optimized original.
        """
        q = quality or self.output_quality
        webp_data = self.convert_to_webp(img, quality=q)

        return ProcessedImageResult(
            variant_name=ImageVariantName.ORIGINAL.value,
            data=webp_data,
            width=img.size[0],
            height=img.size[1],
            content_type="image/webp",
            file_size=len(webp_data),
        )

    def process_image(
        self,
        file_data: bytes,
    ) -> list[ProcessedImageResult]:
        """Full image processing pipeline: validate, strip, normalize, generate variants.

        Processes a raw image through:
        1. Validation (format, size, corruption check)
        2. EXIF stripping (with orientation fix)
        3. Color mode normalization
        4. Original optimization (WebP conversion)
        5. Size variant generation (thumbnail, medium, large)

        Args:
            file_data: Raw image file bytes.

        Returns:
            List of ProcessedImageResult: [original, thumbnail, medium, large].

        Raises:
            ImageProcessingError: If validation or processing fails.
        """
        img = self.validate(file_data)
        img = self.strip_exif(img)
        img = self.normalize_mode(img)

        results: list[ProcessedImageResult] = []

        # Optimize original
        try:
            original = self.optimize_original(img)
            results.append(original)
        except Exception as exc:
            logger.error("Failed to optimize original image: %s", exc)
            raise ImageProcessingError(f"Failed to optimize original: {exc}")

        # Generate size variants
        for variant_config in self.variants:
            try:
                variant = self.process_variant(img, variant_config)
                results.append(variant)
            except Exception as exc:
                logger.error(
                    "Failed to generate variant '%s': %s",
                    variant_config.name,
                    exc,
                )
                # Continue with other variants - don't fail the whole pipeline
                continue

        img.close()

        if len(results) < 2:
            raise ImageProcessingError(
                "Failed to generate enough image variants. "
                "At least original + 1 variant required."
            )

        return results
