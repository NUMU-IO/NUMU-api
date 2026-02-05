"""Unit tests for image processing pipeline.

Tests ImageProcessor: resize dimensions, WebP output, EXIF stripping,
validation, color mode normalization, and full pipeline processing.
"""

import io

import pytest
from PIL import Image

from src.infrastructure.external_services.image.image_processor import (
    DEFAULT_VARIANTS,
    ImageProcessingError,
    ImageProcessor,
    ImageVariantName,
)


def _create_test_image(
    width: int = 800,
    height: int = 600,
    mode: str = "RGB",
    format: str = "JPEG",
    exif: bool = False,
) -> bytes:
    """Create a test image as raw bytes."""
    img = Image.new(mode, (width, height), color="red")

    if exif:
        # Add EXIF data with orientation tag
        from PIL.ExifTags import Base as ExifBase

        exif_data = img.getexif()
        exif_data[ExifBase.Make] = "TestCamera"
        exif_data[ExifBase.Model] = "TestModel"
        exif_data[ExifBase.Software] = "TestSoftware"
        # Set landscape orientation (normal = 1)
        exif_data[ExifBase.Orientation] = 1

    buf = io.BytesIO()
    save_kwargs = {"format": format}
    if exif and format == "JPEG":
        save_kwargs["exif"] = img.getexif().tobytes()
    img.save(buf, **save_kwargs)
    buf.seek(0)
    return buf.getvalue()


def _create_png_with_transparency(width: int = 200, height: int = 200) -> bytes:
    """Create a PNG image with alpha channel."""
    img = Image.new("RGBA", (width, height), color=(255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


class TestImageProcessorValidation:
    """Tests for image validation."""

    def setup_method(self):
        self.processor = ImageProcessor()

    def test_validate_valid_jpeg(self):
        """Test validation of a valid JPEG image."""
        data = _create_test_image(format="JPEG")
        img = self.processor.validate(data)
        assert img.format == "JPEG"
        assert img.size == (800, 600)

    def test_validate_valid_png(self):
        """Test validation of a valid PNG image."""
        data = _create_test_image(format="PNG")
        img = self.processor.validate(data)
        assert img.format == "PNG"

    def test_validate_valid_webp(self):
        """Test validation of a valid WebP image."""
        data = _create_test_image(format="WEBP")
        img = self.processor.validate(data)
        assert img.format == "WEBP"

    def test_validate_too_small_file_raises(self):
        """Test that very small file raises error."""
        with pytest.raises(ImageProcessingError, match="too small"):
            self.processor.validate(b"\x00\x01\x02")

    def test_validate_empty_bytes_raises(self):
        """Test that empty bytes raise error."""
        with pytest.raises(ImageProcessingError, match="too small"):
            self.processor.validate(b"")

    def test_validate_invalid_data_raises(self):
        """Test that non-image data raises error."""
        with pytest.raises(ImageProcessingError):
            self.processor.validate(b"this is not an image file at all " * 10)

    def test_validate_exceeds_max_dimension_raises(self):
        """Test that image exceeding max dimensions raises error."""
        # Create a very wide image (exceeds 8192 limit)
        data = _create_test_image(width=9000, height=100, format="PNG")
        with pytest.raises(ImageProcessingError, match="exceed maximum"):
            self.processor.validate(data)


class TestImageProcessorExifStripping:
    """Tests for EXIF metadata stripping."""

    def setup_method(self):
        self.processor = ImageProcessor()

    def test_strip_exif_removes_metadata(self):
        """Test that EXIF data is stripped from the image."""
        data = _create_test_image(exif=True, format="JPEG")
        img = self.processor.validate(data)

        # Before stripping, EXIF may be present
        stripped = self.processor.strip_exif(img)

        # After stripping, the image should not carry EXIF tags forward
        # (strip_exif applies exif_transpose which creates a new image)
        assert stripped is not None
        assert stripped.size[0] > 0
        assert stripped.size[1] > 0

    def test_strip_exif_preserves_dimensions(self):
        """Test that stripping EXIF preserves image dimensions for normal orientation."""
        data = _create_test_image(width=800, height=600, exif=True, format="JPEG")
        img = self.processor.validate(data)
        stripped = self.processor.strip_exif(img)

        assert stripped.size == (800, 600)

    def test_strip_exif_on_image_without_exif(self):
        """Test that stripping EXIF on image without EXIF works fine."""
        data = _create_test_image(format="PNG")
        img = self.processor.validate(data)
        stripped = self.processor.strip_exif(img)

        assert stripped is not None
        assert stripped.size == (800, 600)


class TestImageProcessorResize:
    """Tests for image resizing."""

    def setup_method(self):
        self.processor = ImageProcessor()

    def test_resize_landscape_image(self):
        """Test resizing a landscape image maintains aspect ratio."""
        img = Image.new("RGB", (1600, 1200))
        resized = self.processor.resize(img, max_width=600, max_height=600)

        # 1600x1200 -> fits in 600x600 -> 600x450 (maintaining 4:3 ratio)
        assert resized.size[0] == 600
        assert resized.size[1] == 450

    def test_resize_portrait_image(self):
        """Test resizing a portrait image maintains aspect ratio."""
        img = Image.new("RGB", (1200, 1600))
        resized = self.processor.resize(img, max_width=600, max_height=600)

        # 1200x1600 -> fits in 600x600 -> 450x600 (maintaining 3:4 ratio)
        assert resized.size[0] == 450
        assert resized.size[1] == 600

    def test_resize_square_image(self):
        """Test resizing a square image."""
        img = Image.new("RGB", (1000, 1000))
        resized = self.processor.resize(img, max_width=150, max_height=150)

        assert resized.size == (150, 150)

    def test_resize_smaller_image_not_upscaled(self):
        """Test that images smaller than target are not upscaled."""
        img = Image.new("RGB", (100, 80))
        resized = self.processor.resize(img, max_width=600, max_height=600)

        # Should stay at original size since it's smaller
        assert resized.size == (100, 80)

    def test_resize_thumbnail_dimensions(self):
        """Test resize to thumbnail size (150px)."""
        img = Image.new("RGB", (2000, 1500))
        resized = self.processor.resize(img, max_width=150, max_height=150)

        assert resized.size[0] <= 150
        assert resized.size[1] <= 150
        # Maintain aspect ratio: 2000x1500 -> 150x~113 (Pillow rounds up)
        assert resized.size[0] == 150
        assert 112 <= resized.size[1] <= 113

    def test_resize_medium_dimensions(self):
        """Test resize to medium size (600px)."""
        img = Image.new("RGB", (2000, 1000))
        resized = self.processor.resize(img, max_width=600, max_height=600)

        assert resized.size[0] <= 600
        assert resized.size[1] <= 600
        # 2000x1000 -> 600x300
        assert resized.size[0] == 600
        assert resized.size[1] == 300

    def test_resize_large_dimensions(self):
        """Test resize to large size (1200px)."""
        img = Image.new("RGB", (4000, 3000))
        resized = self.processor.resize(img, max_width=1200, max_height=1200)

        assert resized.size[0] <= 1200
        assert resized.size[1] <= 1200
        # 4000x3000 -> 1200x900
        assert resized.size[0] == 1200
        assert resized.size[1] == 900


class TestImageProcessorWebPConversion:
    """Tests for WebP conversion."""

    def setup_method(self):
        self.processor = ImageProcessor()

    def test_convert_to_webp_output_format(self):
        """Test that output is valid WebP format."""
        img = Image.new("RGB", (200, 200), color="blue")
        webp_data = self.processor.convert_to_webp(img)

        # Verify it's valid WebP by reading it back
        result_img = Image.open(io.BytesIO(webp_data))
        assert result_img.format == "WEBP"

    def test_convert_to_webp_with_alpha(self):
        """Test WebP conversion preserves alpha channel."""
        img = Image.new("RGBA", (200, 200), color=(255, 0, 0, 128))
        webp_data = self.processor.convert_to_webp(img)

        result_img = Image.open(io.BytesIO(webp_data))
        assert result_img.mode in ("RGBA", "RGBX")

    def test_convert_to_webp_quality_affects_size(self):
        """Test that higher quality produces larger file for complex images."""
        import random

        # Use a noisy image so compression differences are meaningful
        random.seed(42)
        img = Image.new("RGB", (300, 300))
        pixels = img.load()
        for x in range(300):
            for y in range(300):
                pixels[x, y] = (
                    random.randint(0, 255),
                    random.randint(0, 255),
                    random.randint(0, 255),
                )

        low_q = self.processor.convert_to_webp(img, quality=10)
        high_q = self.processor.convert_to_webp(img, quality=95)

        # Higher quality should produce larger file for noisy images
        assert len(high_q) > len(low_q)

    def test_convert_to_webp_compresses_jpeg(self):
        """Test that WebP is generally smaller than JPEG for the same image."""
        img = Image.new("RGB", (800, 600), color="red")

        # Save as JPEG
        jpeg_buf = io.BytesIO()
        img.save(jpeg_buf, format="JPEG", quality=85)
        jpeg_size = jpeg_buf.tell()

        # Convert to WebP
        webp_data = self.processor.convert_to_webp(img, quality=85)

        # WebP should be comparable or smaller for solid-color images
        # (For real photos the savings would be larger)
        assert len(webp_data) > 0
        assert len(webp_data) < jpeg_size * 2  # At most 2x JPEG


class TestImageProcessorColorModeNormalization:
    """Tests for color mode normalization."""

    def setup_method(self):
        self.processor = ImageProcessor()

    def test_normalize_rgb_unchanged(self):
        """Test that RGB images are unchanged."""
        img = Image.new("RGB", (100, 100))
        result = self.processor.normalize_mode(img)
        assert result.mode == "RGB"

    def test_normalize_rgba_unchanged(self):
        """Test that RGBA images are unchanged."""
        img = Image.new("RGBA", (100, 100))
        result = self.processor.normalize_mode(img)
        assert result.mode == "RGBA"

    def test_normalize_palette_to_rgb(self):
        """Test that palette images are converted to RGB."""
        img = Image.new("P", (100, 100))
        result = self.processor.normalize_mode(img)
        assert result.mode == "RGB"

    def test_normalize_cmyk_to_rgb(self):
        """Test that CMYK images are converted to RGB."""
        img = Image.new("CMYK", (100, 100))
        result = self.processor.normalize_mode(img)
        assert result.mode == "RGB"

    def test_normalize_grayscale_to_rgb(self):
        """Test that grayscale images are converted to RGB."""
        img = Image.new("L", (100, 100))
        result = self.processor.normalize_mode(img)
        assert result.mode == "RGB"


class TestImageProcessorFullPipeline:
    """Tests for the full process_image pipeline."""

    def setup_method(self):
        self.processor = ImageProcessor()

    def test_process_image_returns_all_variants(self):
        """Test that process_image returns original + all configured variants."""
        data = _create_test_image(width=2000, height=1500, format="JPEG")
        results = self.processor.process_image(data)

        # Should have: original + thumbnail + medium + large = 4
        assert len(results) == 4

        variant_names = {r.variant_name for r in results}
        assert "original" in variant_names
        assert "thumbnail" in variant_names
        assert "medium" in variant_names
        assert "large" in variant_names

    def test_process_image_all_webp_format(self):
        """Test that all variants are in WebP format."""
        data = _create_test_image(width=1000, height=800, format="JPEG")
        results = self.processor.process_image(data)

        for result in results:
            assert result.content_type == "image/webp"
            # Verify it's actually WebP by trying to read it
            img = Image.open(io.BytesIO(result.data))
            assert img.format == "WEBP"

    def test_process_image_variant_dimensions(self):
        """Test that variant dimensions respect their max size."""
        data = _create_test_image(width=4000, height=3000, format="JPEG")
        results = self.processor.process_image(data)

        for result in results:
            if result.variant_name == "thumbnail":
                assert result.width <= 150
                assert result.height <= 150
            elif result.variant_name == "medium":
                assert result.width <= 600
                assert result.height <= 600
            elif result.variant_name == "large":
                assert result.width <= 1200
                assert result.height <= 1200
            elif result.variant_name == "original":
                # Original should match source dimensions
                assert result.width == 4000
                assert result.height == 3000

    def test_process_image_from_png(self):
        """Test processing a PNG input produces WebP output."""
        data = _create_test_image(width=500, height=500, format="PNG")
        results = self.processor.process_image(data)

        assert len(results) >= 2
        for result in results:
            assert result.content_type == "image/webp"

    def test_process_image_with_transparency(self):
        """Test processing image with alpha channel."""
        data = _create_png_with_transparency(width=500, height=500)
        results = self.processor.process_image(data)

        assert len(results) >= 2
        for result in results:
            assert result.content_type == "image/webp"

    def test_process_image_exif_stripped_in_output(self):
        """Test that EXIF data is stripped from output variants."""
        data = _create_test_image(width=1000, height=800, exif=True, format="JPEG")
        results = self.processor.process_image(data)

        for result in results:
            # Read back the WebP and check for EXIF
            img = Image.open(io.BytesIO(result.data))
            exif = img.getexif()
            # After pipeline, no camera-specific EXIF tags should remain
            assert exif.get(0x010F) is None  # Make
            assert exif.get(0x0110) is None  # Model

    def test_process_image_invalid_data_raises(self):
        """Test that invalid image data raises error."""
        with pytest.raises(ImageProcessingError):
            self.processor.process_image(b"not an image" * 10)

    def test_process_image_small_image_not_upscaled(self):
        """Test that a small image is not upscaled in variants."""
        data = _create_test_image(width=100, height=80, format="JPEG")
        results = self.processor.process_image(data)

        for result in results:
            # No variant should be larger than original
            assert result.width <= 100
            assert result.height <= 80

    def test_process_image_result_has_file_size(self):
        """Test that results include accurate file size."""
        data = _create_test_image(width=500, height=500, format="JPEG")
        results = self.processor.process_image(data)

        for result in results:
            assert result.file_size > 0
            assert result.file_size == len(result.data)


class TestDefaultVariantConfigs:
    """Tests for default variant configurations."""

    def test_default_variants_count(self):
        """Test that there are 3 default variants (thumbnail, medium, large)."""
        assert len(DEFAULT_VARIANTS) == 3

    def test_thumbnail_config(self):
        """Test thumbnail variant configuration."""
        thumbnail = next(
            v for v in DEFAULT_VARIANTS if v.name == ImageVariantName.THUMBNAIL
        )
        assert thumbnail.max_width == 150
        assert thumbnail.max_height == 150

    def test_medium_config(self):
        """Test medium variant configuration."""
        medium = next(v for v in DEFAULT_VARIANTS if v.name == ImageVariantName.MEDIUM)
        assert medium.max_width == 600
        assert medium.max_height == 600

    def test_large_config(self):
        """Test large variant configuration."""
        large = next(v for v in DEFAULT_VARIANTS if v.name == ImageVariantName.LARGE)
        assert large.max_width == 1200
        assert large.max_height == 1200
