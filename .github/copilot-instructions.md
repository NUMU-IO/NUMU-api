# Octyrafiy Backend - Copilot Instructions

## Architecture Overview

This is a **Clean Architecture** Python backend (FastAPI) for an e-commerce platform. The architecture enforces strict dependency rules: outer layers depend on inner layers, never the reverse.

```
src/
├── api/           → Presentation layer (routes, schemas, middleware)
├── application/   → Use cases & DTOs (business logic orchestration)
├── core/          → Domain layer (entities, interfaces, value objects)
├── infrastructure/→ External concerns (DB, APIs, cache, messaging)
└── config/        → Application configuration
```

### Layer Dependency Rules
- **core/** → No dependencies on other layers (pure domain)
- **application/** → Depends only on `core/`
- **infrastructure/** → Implements interfaces from `core/interfaces/`
- **api/** → Orchestrates `application/` use cases via dependency injection

## Domain Modules

Business domains are organized by feature in `application/use_cases/`:
- `auth/` - Authentication & authorization
- `customers/` - Customer management
- `orders/` - Order processing
- `products/` - Product catalog
- `stores/` - Store/merchant management
- `ai/` - AI-powered features (likely OpenAI integration)

## External Service Integrations

Wrappers in `infrastructure/external_services/`:
| Service | Purpose |
|---------|---------|
| `stripe/` | Payment processing |
| `tap/` | Alternative payment gateway (likely MENA region) |
| `shippo/` | Shipping & logistics |
| `openai/` | AI features |
| `resend/` | Email delivery |
| `cloudflare_r2/` | Object storage (images, files) |

## Key Conventions

### Repository Pattern
- Define interfaces in `core/interfaces/repositories/`
- Implement in `infrastructure/repositories/`
- Inject via `api/dependencies/`

### Use Case Pattern
- Each use case is a single-purpose class in `application/use_cases/{domain}/`
- Use cases receive repository/service interfaces via constructor injection
- Return DTOs from `application/dto/`, never raw entities

### API Structure
- Routes: `api/v1/routes/` - Versioned endpoints
- Schemas: `api/v1/schemas/` - Pydantic request/response models
- Responses: `api/responses/` - Standardized response formats

### Database
- ORM models: `infrastructure/database/models/`
- Migrations: `alembic/versions/` (run with `alembic upgrade head`)
- Connection setup: `infrastructure/database/`

## Testing Structure

```
tests/
├── unit/          → Test isolated components (mock dependencies)
├── integration/   → Test with real DB/services
└── e2e/           → Full API flow tests
```

## Developer Workflow

- **Seed data**: `python scripts/seed_data.py`
- **Docker**: Build with `docker/Dockerfile`
- **Migrations**: Use Alembic (`alembic revision --autogenerate -m "..."`)

## When Adding Features

1. Define entity in `core/entities/`
2. Create repository interface in `core/interfaces/repositories/`
3. Implement repository in `infrastructure/repositories/`
4. Write use case in `application/use_cases/{domain}/`
5. Add route in `api/v1/routes/` with Pydantic schemas
6. Wire dependencies in `api/dependencies/`
