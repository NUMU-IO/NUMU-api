# @numu/api-types

Auto-generated TypeScript types from the NUMU API OpenAPI specification.

## Usage

```typescript
import type { paths, components } from "@numu/api-types";

// Access request/response types via operation paths
type CreateProductRequest = components["schemas"]["CreateProductRequest"];
type ProductResponse = components["schemas"]["ProductResponse"];

// Type-safe API paths
type ProductsPath = paths["/api/v1/stores/{store_id}/products"]["get"];
```

## Generation

Types are generated from the live OpenAPI spec using [openapi-typescript](https://openapi-ts.dev/).

```bash
# From the running backend (default)
npm run generate

# From a local openapi.json file
npm run generate:file
```

## CI Integration

Types are automatically regenerated in CI when the backend schema changes. See the `generate-types` job in `.github/workflows/ci.yml`.
