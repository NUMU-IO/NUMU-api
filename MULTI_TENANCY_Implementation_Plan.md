# Octyrafiy Multi-Tenancy Implementation Plan

This plan transforms the current single-tenant Octyrafiy backend into a Schema-Per-Tenant multi-tenant architecture.

## Phase 1: Infrastructure & Core Setup

### 1.1 Create Tenant Module Structure
We need a dedicated module to manage tenants in the `public` schema.

- [ ] Create directory: `src/tenants/`
- [ ] Create file: `src/tenants/models.py` (SQLAlchemy model for `tenants` table)
- [ ] Create file: `src/tenants/repository.py` (Repo to fetching tenants by domain)
- [ ] Create file: `src/tenants/service.py` (Logic to create tenant & provision schema)

### 1.2 Update Database Session Logic
The database session must dynamically switch schemas based on the current request context.

- [ ] Modify `src/infrastructure/database/connection.py` (or rename to `session.py` to match instructions):
    - Add `contextvars` for `tenant_schema`
    - Create `set_tenant_schema` and `get_tenant_schema` functions
    - Update `get_db_session` to execute `SET search_path TO {schema}`

### 1.3 Create Tenant Middleware
This middleware will intercept requests, identify the tenant from the subdomain, and set the database context.

- [ ] Create file: `src/api/middleware/tenant_middleware.py`
    - Extract subdomain from `Host` header
    - Lookup tenant in `TenantRepository`
    - Call `set_tenant_schema`

### 1.4 Register Middleware
- [ ] Update `src/main.py` to include `TenantMiddleware`.

## Phase 2: Tenant Management & Provisioning

### 2.1 Implement Tenant Creation
We need a way to register new tenants (which creates their database schema).

- [ ] Implement `create_tenant` method in `src/tenants/service.py`:
    - Insert into `public.tenants`
    - Run raw SQL: `CREATE SCHEMA IF NOT EXISTS {schema_name}`
    - Run `Base.metadata.create_all` bound to that schema (for initial prototyping) or setup Alembic.

### 2.2 Public API for Tenant Registration
- [ ] Create route `src/api/v1/routes/tenants.py` (or `admin.py`) for superadmin to create stores/tenants.

## Phase 3: Domain Refactoring (Splitting Public vs Tenant)

### 3.1 Separate Models
Decide which models live in `public` and which live in `tenant_schema`.
- **Public**: `Tenant`, `User` (if implementing single sign-on across stores, otherwise Users might be per-tenant).
- **Tenant**: `Product`, `Order`, `Customer`, `Store` (Store details).

- [ ] Ensure `src/infrastructure/database/models/` models inherit from a Base that doesn't hardcode schemas, or are strictly used within the tenant context.

## Phase 4: Migrations (Advanced)

### 4.1 Update Alembic
- [ ] Modify `alembic/env.py` to handle schema iteration if you want one migration script to apply to all tenants, or use a dynamic schema approach. *For MVP, `create_all` in the provisioning service is often easier.*

## Execution Checklist

1.  **Stop the server**.
2.  **Backup database** (if any data exists).
3.  **Run the file creation & edits** for the new modules.
4.  **Update `main.py`**.
5.  **Run a script** to create the 'public' schema tables (Tenants).
6.  **Test** by hitting a subdomain (e.g., `store1.localhost`) and verifying it tries to load the correct schema.
