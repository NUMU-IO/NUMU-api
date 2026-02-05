"""Celery tasks for asynchronous image processing.

Provides background optimization for product image uploads,
including single and bulk image processing with WebP conversion
and multi-size variant generation.
"""

import asyncio
import base64
from uuid import UUID

from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)


def _run_async(coro):
    """Run async code in Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _process_and_upload_image(
    file_data: bytes,
    product_id: UUID,
    original_filename: str,
) -> dict:
    """Async helper that uses the existing ImagePipeline."""
    from src.infrastructure.external_services.cloudflare_r2 import (
        CloudflareR2StorageService,
    )
    from src.infrastructure.external_services.image.image_pipeline import ImagePipeline
    from src.infrastructure.external_services.image.image_processor import ImageProcessor

    processor = ImageProcessor()
    storage = CloudflareR2StorageService()
    pipeline = ImagePipeline(
        image_processor=processor,
        storage_service=storage,
    )

    result = await pipeline.process_and_upload(
        file_data=file_data,
        product_id=product_id,
        original_filename=original_filename,
    )

    return {
        "url": result.url,
        "key": result.key,
        "total_size": result.total_size,
        "variant_urls": result.variant_urls,
        "variant_keys": result.variant_keys,
    }


@celery_app.task(
    name="tasks.process_product_image",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=120,
    time_limit=180,
)
def process_product_image_task(
    self,
    file_data_b64: str,
    product_id: str,
    original_filename: str,
    store_id: str | None = None,
) -> dict:
    """Process and optimize a single product image in the background.

    Accepts base64-encoded image data (since Celery JSON serializer
    cannot handle raw bytes), processes it through the image pipeline
    (validate, strip EXIF, WebP conversion, multi-size variants),
    and uploads all variants to R2 storage.

    Args:
        file_data_b64: Base64-encoded raw image bytes.
        product_id: Product UUID string.
        original_filename: Original filename from upload.
        store_id: Optional store UUID string for logging context.

    Returns:
        Dict with url, key, total_size, variant_urls, variant_keys.

    Raises:
        ImageProcessingError: If image is invalid or corrupt (no retry).
        IOError/ConnectionError: Transient failures (retried automatically).
    """
    from src.infrastructure.external_services.image.image_processor import (
        ImageProcessingError,
    )

    logger.info(
        "image_task_started",
        product_id=product_id,
        filename=original_filename,
        store_id=store_id,
        attempt=self.request.retries + 1,
    )

    try:
        file_data = base64.b64decode(file_data_b64)
    except Exception as exc:
        logger.error(
            "image_task_decode_failed",
            product_id=product_id,
            error=str(exc),
        )
        raise ValueError(f"Failed to decode base64 image data: {exc}")

    try:
        result = _run_async(
            _process_and_upload_image(
                file_data=file_data,
                product_id=UUID(product_id),
                original_filename=original_filename,
            )
        )

        logger.info(
            "image_task_completed",
            product_id=product_id,
            url=result["url"],
            total_size=result["total_size"],
            variants=list(result["variant_urls"].keys()),
        )
        return result

    except ImageProcessingError as exc:
        # Permanent failure -- invalid/corrupt image, do not retry
        logger.error(
            "image_task_processing_failed",
            product_id=product_id,
            error=str(exc),
        )
        return {
            "status": "failed",
            "error": str(exc),
            "product_id": product_id,
            "retryable": False,
        }

    except (IOError, ConnectionError, TimeoutError) as exc:
        # Transient failure -- retry with backoff
        logger.warning(
            "image_task_transient_failure",
            product_id=product_id,
            error=str(exc),
            attempt=self.request.retries + 1,
            max_retries=self.max_retries,
        )
        raise self.retry(exc=exc)

    except Exception as exc:
        logger.error(
            "image_task_unexpected_error",
            product_id=product_id,
            error=str(exc),
            exc_info=True,
        )
        raise self.retry(exc=exc)


@celery_app.task(
    name="tasks.process_bulk_product_images",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=600,
    time_limit=720,
)
def process_bulk_product_images_task(
    self,
    images: list[dict],
    product_id: str,
    store_id: str | None = None,
) -> dict:
    """Process multiple product images in a single task.

    Dispatches individual image processing sub-tasks via Celery group
    for parallel execution, then collects results. Each image in the
    list should be a dict with:
        - file_data_b64: str (base64-encoded image bytes)
        - original_filename: str

    Args:
        images: List of dicts with file_data_b64 and original_filename.
        product_id: Product UUID string.
        store_id: Optional store UUID string for logging context.

    Returns:
        Dict with processed (count), failed (count), and results (list).
    """
    from celery import group

    logger.info(
        "bulk_image_task_started",
        product_id=product_id,
        image_count=len(images),
        store_id=store_id,
    )

    if not images:
        return {"processed": 0, "failed": 0, "results": []}

    # Dispatch individual image tasks as a group
    tasks = [
        process_product_image_task.s(
            file_data_b64=img["file_data_b64"],
            product_id=product_id,
            original_filename=img["original_filename"],
            store_id=store_id,
        )
        for img in images
    ]

    job = group(tasks)
    group_result = job.apply_async()

    try:
        # Wait for all tasks with a timeout
        results = group_result.get(
            timeout=540,  # 9 minutes (under our 10-min soft limit)
            propagate=False,
        )
    except Exception as exc:
        logger.error(
            "bulk_image_task_timeout",
            product_id=product_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)

    processed = 0
    failed = 0
    successful_results = []

    for result in results:
        if isinstance(result, Exception):
            failed += 1
            logger.warning(
                "bulk_image_individual_failure",
                product_id=product_id,
                error=str(result),
            )
        elif isinstance(result, dict) and result.get("status") == "failed":
            failed += 1
        else:
            processed += 1
            successful_results.append(result)

    logger.info(
        "bulk_image_task_completed",
        product_id=product_id,
        processed=processed,
        failed=failed,
        total=len(images),
    )

    return {
        "processed": processed,
        "failed": failed,
        "results": successful_results,
    }
