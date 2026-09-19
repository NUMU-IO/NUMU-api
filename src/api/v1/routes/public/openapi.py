"""The public API contract — the subset a third party can actually call.

The full schema (843 paths, admin and staff included) is a map of the inside
of the platform and is never served in production. What a developer needs is
the part their token can reach, which is already defined exactly once, by the
scope map that guards every token request. Deriving the document from that map
rather than hand-listing paths means a route can never appear here while being
403 in practice, and a new store route joins the docs the moment it is given a
scope domain.
"""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from src.application.services.personal_access_token_service import (
    SCOPE_DOMAINS,
    required_scope_for,
)
from src.config import settings

router = APIRouter(tags=["Public"])

_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# Operations are grouped by the scope domain that guards them, not by the
# router they live in: docs tools turn tags into folders, and "Store Product
# Variants" or "Store Themes V2" mirror the code layout rather than what a
# developer is looking for. Listed in reading order; the published docs link
# to these names, so renaming one breaks those links.
_DOMAIN_TAGS = (
    (
        "catalog",
        "Catalog",
        "Products, variants, inventory, categories, bundles and gift cards.",
    ),
    (
        "orders",
        "Orders & fulfillment",
        "Orders, shipments, waybills, returns, refunds and abandoned checkouts.",
    ),
    ("customers", "Customers", "Customer records and their addresses."),
    (
        "marketing",
        "Marketing",
        "Coupons, promotions, campaigns, WhatsApp and the inbox.",
    ),
    ("analytics", "Analytics", "Store metrics, reports and dashboard figures."),
    ("themes", "Online store", "Themes, pages, menus and the editor."),
    (
        "settings",
        "Settings & money",
        "Store settings, locations, shipping, payments and invoices.",
    ),
    ("media", "Media", "File uploads and stored assets."),
    ("risk", "Risk", "Risk assessments for orders."),
    ("any", "Identity", "Who a token belongs to. Reachable with any valid token."),
)
_TAG_BY_DOMAIN = {domain: name for domain, name, _ in _DOMAIN_TAGS}

_DESCRIPTION = f"""
The NUMU merchant API, as a third party can use it.

**Authentication.** Mint a personal access token in the merchant hub
(`POST /api/v1/stores/{{store_id}}/access-tokens`) and send it as
`Authorization: Bearer numu_pat_...`. The secret is shown once. A token is
pinned to one store and carries scopes; anything outside them answers 403.

**Scopes.** `{{domain}}:read` or `{{domain}}:write`, where domain is one of:
{", ".join(f"`{d}`" for d in SCOPE_DOMAINS)}. Each operation below lists the
scope it needs as `x-numu-scope`.

**Limits.** 300 requests/minute per token. Responses are JSON, and errors use
standard HTTP status codes.

Endpoints not listed here (admin, staff, billing, internal) are not callable
with a token, by design.
"""

_cache: dict[str, Any] = {}


def _referenced_schemas(node: Any, found: set[str]) -> None:
    """Collect every `#/components/schemas/X` name reachable from ``node``."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            _referenced_schemas(value, found)
    elif isinstance(node, list):
        for value in node:
            _referenced_schemas(value, found)


def build_public_schema(full: dict[str, Any]) -> dict[str, Any]:
    """Filter a full OpenAPI document down to the token-reachable surface."""
    paths: dict[str, Any] = {}
    for path, operations in full.get("paths", {}).items():
        kept = {}
        for method, operation in operations.items():
            if method not in _METHODS:
                continue
            scope = required_scope_for(path, method)
            if scope is None:
                continue
            scope = "any" if scope == "__identity__" else scope
            kept[method] = {
                **operation,
                "tags": [_TAG_BY_DOMAIN[scope.split(":")[0]]],
                "x-numu-scope": scope,
                "security": [{"bearerAuth": []}],
            }
        if kept:
            # Path-level keys (e.g. shared parameters) ride along with the
            # operations that survived.
            shared = {k: v for k, v in operations.items() if k not in _METHODS}
            paths[path] = {**shared, **kept}

    # Ship only the schemas those paths actually reference: the components
    # block is otherwise a field-by-field description of the admin surface.
    wanted: set[str] = set()
    _referenced_schemas(paths, wanted)
    all_schemas = full.get("components", {}).get("schemas", {})
    seen: set[str] = set()
    while wanted - seen:
        name = (wanted - seen).pop()
        seen.add(name)
        _referenced_schemas(all_schemas.get(name, {}), wanted)

    used_tags = {
        operation["tags"][0]
        for operations in paths.values()
        for method, operation in operations.items()
        if method in _METHODS
    }

    return {
        **{
            k: v
            for k, v in full.items()
            if k not in ("paths", "components", "info", "tags", "servers", "security")
        },
        "info": {
            "title": "NUMU API",
            "version": full.get("info", {}).get("version", "1"),
            "description": _DESCRIPTION.strip(),
        },
        # Paths already start with /api/v1, so the server is the bare host.
        "servers": [
            {"url": (settings.public_api_url or "https://numueg.app").rstrip("/")}
        ],
        "security": [{"bearerAuth": []}],
        "tags": [
            {"name": name, "description": description}
            for _, name, description in _DOMAIN_TAGS
            if name in used_tags
        ],
        "paths": paths,
        "components": {
            "schemas": {n: all_schemas[n] for n in sorted(seen) if n in all_schemas},
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "A personal access token (`numu_pat_...`).",
                }
            },
        },
    }


@router.get("/openapi.json", include_in_schema=False)
async def public_openapi(request: Request) -> JSONResponse:
    """The public contract, as OpenAPI 3.1 — generate a client from it."""
    if "schema" not in _cache:
        _cache["schema"] = build_public_schema(request.app.openapi())
    return JSONResponse(_cache["schema"])


@router.get("/docs", include_in_schema=False)
async def public_docs() -> HTMLResponse:
    """Swagger UI over the public contract."""
    return get_swagger_ui_html(
        openapi_url="/api/v1/public/openapi.json",
        title="NUMU API — developer reference",
    )
