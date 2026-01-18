# NUMU Backend - AI Coding Agent Instructions

## Architecture Overview

**NUMU** is an e-commerce platform API using **Clean Architecture** with strict layer separation and **schema-per-tenant multi-tenancy**. Dependencies flow inward: outer layers depend on inner layers, never the reverse.

```
src/
├── api/           → Presentation (routes, schemas, middleware)
├── application/   → Use cases & DTOs (business logic orchestration)  
├── core/          → Domain (entities, interfaces, value objects)
├── infrastructure/→ External concerns (DB, repos, cache, services)
└── config/        → Settings from environment
```

**Dependency Rules:**
- `core/` is pure—no dependencies on other layers
- `application/` depends **only on** `core/`
- `infrastructure/` implements interfaces from `core/interfaces/`
- `api/` wires dependencies and orchestrates `application/` use cases

## Multi-Tenancy (Schema-Per-Tenant)

NUMU isolates data by tenant using PostgreSQL schemas. Critical request flow:

1. **Request arrives** → `TenantMiddleware` extracts subdomain from Host header
2. **Lookup tenant** → `TenantRepository` queries `public.tenants` table
3. **Set context** → `set_tenant_schema(tenant.schema_name)` switches search_path to tenant's schema
4. **Use case runs** → All queries automatically use tenant's isolated data
5. **Response** → `reset_tenant_schema()` cleans context (in finally block)

**Key files:**
- `src/api/middleware/tenant_middleware.py` — Extract subdomain, lookup, set schema
- `src/infrastructure/tenancy/` — Tenant repo, service, context management
- `src/infrastructure/database/connection.py` — `set_tenant_schema()`, `reset_tenant_schema()`
- `alembic/versions/001_create_tenants.py` — Public schema setup

**Exception:** Public routes (auth signup, /health) skip tenant lookup and remain in public schema.

## Entity & Repository Pattern

**Entities** (domain objects) in `core/entities/` never reference database models.

**Repository interfaces** in `core/interfaces/repositories/` define contracts. Implementations in `infrastructure/repositories/` map SQLAlchemy models ↔ domain entities:

```python
class UserRepository(IUserRepository):
    def __init__(self, session: AsyncSession):
        self.session = session
    
    def _to_entity(self, model: UserModel) -> User:
        return User(id=model.id, email=Email(model.email), ...)
    
    def _to_model(self, entity: User) -> UserModel:
        return UserModel(id=entity.id, email=str(entity.email), ...)
    
    async def get_by_id(self, entity_id: UUID) -> User | None:
        result = await self.session.execute(select(UserModel).where(...))
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None
```

**Always use `_to_entity()` and `_to_model()` helpers** to enforce domain/persistence separation.

## Use Case Pattern

Each use case in `application/use_cases/{domain}/` is a single-responsibility class:
1. Receives dependencies via constructor (repos, services)
2. Orchestrates business logic
3. Returns DTOs from `application/dto/`, never raw entities

```python
class GetCurrentUserUseCase:
    def __init__(self, user_repo: IUserRepository):
        self.user_repo = user_repo
    
    async def execute(self, user_id: UUID) -> UserDTO:
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(user_id)
        return UserDTO.from_entity(user)
```

## Dependency Injection via API Layer

`src/api/dependencies/` wires all dependencies for routes.

**repositories.py** example:
```python
def get_user_repository(session: Annotated[AsyncSession, Depends(get_db)]) -> UserRepository:
    return UserRepository(session)
```

**Routes** inject dependencies and call use cases:
```python
@router.post("/login")
async def login(
    request: LoginRequest,
    use_case: Annotated[LoginUseCase, Depends(get_login_use_case)],
):
    dto = await use_case.execute(request.email, request.password)
    return LoginResponse(**dto.dict())
```

## Value Objects

Immutable domain types in `core/value_objects/` (Email, PhoneNumber, Money, Address). Always validate in `__init__()`. Repositories convert them: `Email(model.email)` in `_to_entity()`, `str(entity.email)` in `_to_model()`.

## Database & Migrations

**Connection:** `src/infrastructure/database/connection.py`
- `AsyncSessionLocal` — Async session factory
- `get_db()` → FastAPI dependency for routes
- `set_tenant_schema()` / `reset_tenant_schema()` → Context switching

**Models:** `src/infrastructure/database/models/`
- SQLAlchemy ORM models (NOT entities)
- Include Pydantic validators for business rules

**Migrations (Alembic):**
```bash
alembic revision --autogenerate -m "add_user_table"
alembic upgrade head
alembic downgrade -1
```

For tenant schema provisioning, see `src/infrastructure/tenancy/service.py` — calls `CREATE SCHEMA` and runs migrations.

## Async/Await Convention

All database, I/O, and service calls are **async**. Never block in async contexts.

## External Service Integrations

Wrappers in `infrastructure/external_services/`:
- `stripe/` — Payment processing
- `openai/` — AI features  
- `resend/` — Email delivery
- `cloudflare_r2/` — Object storage (boto3)
- `shippo/` — Shipping & logistics
- `tap/` — Alternative payments (MENA)

Implement domain interfaces, not monolithic adapters.

## Testing Structure

```
tests/
├── unit/          → Isolated components (mock repos/services)
├── integration/   → Real DB, services
└── e2e/           → Full API flows
```

**Commands:**
```bash
make test              # All tests
make test-cov          # With coverage
pytest tests/unit/     # Unit only
```

## Development Workflow

**Setup:**
```bash
pip install -e ".[dev]"
docker-compose -f docker/docker-compose.yml up -d db redis
alembic upgrade head
python scripts/seed_data.py  # Optional
```

**Development:**
```bash
make run         # uvicorn --reload on :8000
make lint        # ruff check
make format      # ruff format
make type-check  # mypy
```

**API Docs:** http://localhost:8000/docs

## Configuration

`src/config/settings.py` loads from `.env`. Key vars:
- `DATABASE_URL` — PostgreSQL async (asyncpg dialect)
- `REDIS_URL` — Cache & sessions
- `JWT_SECRET_KEY` — Change in production!
- `STRIPE_SECRET_KEY`, `OPENAI_API_KEY` — External credentials
- `CORS_ORIGINS` — Security

## Multi-Tenancy Gotchas

1. **Public routes** must explicitly `SET search_path TO public` if querying public schema
2. **TenantMiddleware PUBLIC_PATHS** must include all public endpoints—missing one = data leak
3. **Always reset_tenant_schema() in finally blocks** to prevent context bleed

## When Adding Features

1. Define entity in `core/entities/`
2. Create repository interface in `core/interfaces/repositories/`
3. Implement repository in `infrastructure/repositories/`
4. Write use case in `application/use_cases/{domain}/`
5. Add route in `api/v1/routes/{type}/` with Pydantic schemas
6. Wire dependencies in `api/dependencies/`

Domain modules: `auth/`, `customers/`, `orders/`, `products/`, `stores/`, `ai/`
