"""Order import routes — CSV upload, mapping review, execute.

Flow:
  POST /stores/{id}/orders/import/preview    (multipart file) → columns + mapping
  POST /stores/{id}/orders/import/execute    (file + confirmed mapping) → results
  PUT  /stores/{id}/settings/order-import-mapping  persist merchant's chosen mapping

The preview endpoint returns a synonyms-based mapping suggestion; the merchant
reviews/adjusts it and re-sends the file with the final mapping. Saved mapping
(if any) from ``store.settings["order_import_mapping"]`` is overlayed so the
second upload is near-zero-click.
"""

import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from src.api.dependencies import (
    get_customer_repository,
    get_order_repository,
    get_store_repository,
    verify_store_ownership,
)
from src.api.responses import SuccessResponse
from src.api.utils.upload_validation import validate_csv_upload
from src.application.services.order_import_service import (
    SETTINGS_MAPPING_KEY,
    TARGET_FIELDS,
    OrderImportService,
)
from src.core.entities.store import Store
from src.infrastructure.repositories import (
    CustomerRepository,
    OrderRepository,
    StoreRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/orders/import")


@router.post(
    "/preview",
    summary="Preview a CSV order import (parses headers + suggests mapping)",
    operation_id="preview_order_import",
)
async def preview_order_import(
    store_id: UUID,
    file: Annotated[UploadFile, File(description="CSV file")],
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Return column list, first sample rows, and a suggested column→field map."""
    csv_bytes = await validate_csv_upload(file)
    service = OrderImportService(order_repo, customer_repo, store_repo)
    suggestion = service.suggest(csv_bytes, store.settings)
    return SuccessResponse(
        data={
            "columns": suggestion.columns,
            "sample_rows": suggestion.sample_rows,
            "suggested_mapping": suggestion.suggested_mapping,
            "target_fields": suggestion.target_fields,
        },
        message="Mapping suggested",
    )


@router.post(
    "/execute",
    summary="Execute a CSV order import with a confirmed mapping",
    operation_id="execute_order_import",
)
async def execute_order_import(
    store_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    file: Annotated[UploadFile, File(description="CSV file")],
    mapping_json: Annotated[
        str, Form(description="JSON: {csv_column: target_field | ''}")
    ],
    save_mapping: Annotated[bool, Form()] = True,
):
    """Run the import. Partial-success: returns per-row errors without rollback."""
    csv_bytes = await validate_csv_upload(file)
    try:
        mapping = json.loads(mapping_json)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mapping_json must be valid JSON",
        )
    if not isinstance(mapping, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mapping_json must be an object of {column: field}",
        )

    # Strip empty-string values (unmapped columns) — treat them as absent.
    mapping = {
        str(col): str(field)
        for col, field in mapping.items()
        if field and str(field) in TARGET_FIELDS
    }

    service = OrderImportService(order_repo, customer_repo, store_repo)
    try:
        result = await service.import_rows(csv_bytes, mapping, store.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Persist the mapping so the merchant's next upload prefills automatically.
    if save_mapping:
        settings_blob = dict(store.settings or {})
        settings_blob[SETTINGS_MAPPING_KEY] = mapping
        store.settings = settings_blob
        try:
            await store_repo.update(store)
        except Exception:
            logger.exception("order_import_save_mapping_failed store=%s", str(store.id))

    return SuccessResponse(
        data={
            "total_rows": result.total_rows,
            "created": result.created,
            "skipped": result.skipped,
            "errors": [{"row": e.row, "reason": e.reason} for e in result.errors],
        },
        message="Import complete",
    )


@router.get(
    "/target-fields",
    summary="List the target fields the importer understands",
    operation_id="list_order_import_target_fields",
)
async def list_target_fields(
    store_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
):
    return SuccessResponse(
        data={"target_fields": list(TARGET_FIELDS)},
        message="Target fields",
    )
