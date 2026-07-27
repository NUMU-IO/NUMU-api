# NUMU API

The core backend for **NUMU** — a multi-tenant SaaS e-commerce platform purpose-built for the Egyptian and MENA market (*"Shopify for Egypt"*). Built with FastAPI and **Clean Architecture**, with PostgreSQL Row-Level Security for tenant isolation and a Celery worker fleet for background jobs.

Every NUMU frontend (merchant hub, admin backoffice, customer storefront, landing page) talks to this single API.

---

## Table of contents

- [System architecture](#system-architecture)
- [Clean Architecture layering](#clean-architecture-layering)
- [Request lifecycle](#request-lifecycle)
- [Multi-tenancy](#multi-tenancy)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [API docs](#api-docs)
- [Configuration](#configuration)
- [External services](#external-services)
- [Background jobs](#background-jobs)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Development tools](#development-tools)

---

## System architecture

```mermaid
flowchart TB
  subgraph Clients["NUMU clients"]
    LP[numu-landing-page]
    MH[numo-merchant-hub]
    AD[numu-admin]
    SF[numu-storefront · V3 BYOT]
    BZ[numu-egyptian-bazaar · V2]
    MA[numu-merchant-app · Expo]
    PI[numu-payments-intelligence · Shopify app]
  end

  subgraph API["NUMU-api · FastAPI"]
    HTTP[HTTP layer<br/>routes · middleware]
    APP[Application layer<br/>use cases · DTOs]
    CORE[Core domain<br/>entities · value objects]
    INFRA[Infrastructure<br/>repositories · adapters]
  end

  subgraph Storage["Storage"]
    DB[(PostgreSQL · RLS)]
    R[(Redis · cache + Celery broker)]
    OS[(Object storage · R2/S3/MinIO)]
  end

  subgraph Workers["Celery workers"]
    W1[default queue]
    W2[images queue]
    W3[notifications]
  end

  subgraph External["External services"]
    PM[Paymob · Fawry · InstaPay<br/>Kashier · Moyasar · Tap · JT · Stripe]
    BO[Bosta · MyLerz · Shippo]
    RS[Resend · Twilio]
    WA[WhatsApp Business]
    MT[Meta · Pixel/CAPI/OAuth]
    OAI[OpenAI · HF Vision OCR]
    ETA[ETA Egypt · ZATCA Saudi · Fawaterak]
    SE[Sentry / Slack]
  end

  Clients -- "REST · httpOnly cookies" --> HTTP
  HTTP --> APP --> CORE
  APP --> INFRA
  INFRA --> DB
  INFRA --> R
  INFRA --> OS
  R --> Workers
  Workers --> RS
  Workers --> WA
  Workers --> SE
  INFRA --> PM
  INFRA --> BO
  INFRA --> MT
  INFRA --> OAI
  INFRA --> ETA
```

---

## Clean Architecture layering

Strict, one-way dependencies — **never** reverse them.

```mermaid
flowchart LR
  subgraph L["Dependency direction"]
    direction LR
    api[api/<br/>routes · middleware] --> application[application/<br/>use cases · DTOs] --> core[core/<br/>entities · interfaces]
    api --> infrastructure[infrastructure/<br/>DB · adapters · cache]
    application --> infrastructure
    infrastructure -.implements.-> core
  end
```

| Layer | Responsibility | Allowed imports |
|-------|----------------|-----------------|
| `core/` | Pure domain — entities, value objects, exceptions, interfaces | (nothing internal) |
| `application/` | Use cases + DTOs | `core/` only |
| `infrastructure/` | DB models, repositories, external services, cache, Celery tasks | `core/` (implements interfaces) |
| `api/` | HTTP routes, middleware, FastAPI deps, Pydantic schemas | All inner layers via DI |

---

## Request lifecycle

```mermaid
sequenceDiagram
    actor U as Client
    participant MW as Middleware stack
    participant R as FastAPI route
    participant UC as Use case
    participant Repo as Repository
    participant DB as PostgreSQL (RLS)
    participant C as Redis
    participant E as External service

    U->>MW: HTTP request (host: shop.numueg.app)
    Note over MW: CORS → Logging → Tenant → CSRF<br/>ResponseTime → Sentry → RateLimit<br/>Compression → CacheHeaders → SecurityHeaders
    MW->>MW: resolve tenant from subdomain<br/>SET app.current_tenant
    MW->>R: dispatch
    R->>UC: validate Pydantic schema · invoke use case
    UC->>Repo: load entities
    Repo->>DB: SELECT … (RLS filters by tenant)
    DB-->>Repo: rows
    UC->>C: read/write cache (optional)
    UC->>E: outbound call (optional · Paymob, OpenAI, …)
    UC-->>R: result DTO
    R-->>MW: serialize response
    MW-->>U: HTTP response (with security + cache headers)
```

---

## Multi-tenancy

Shared schema + **PostgreSQL Row-Level Security**. Every tenant-scoped model carries a `tenant_id` foreign key, and the tenant middleware sets `app.current_tenant` per-request so RLS policies filter automatically.

```mermaid
flowchart LR
  Sub["shop.numueg.app"] --> M[TenantMiddleware]
  M -- "resolve subdomain" --> T[(public.tenants)]
  M -- "SET app.current_tenant = ..." --> Conn[(per-request DB connection)]
  Conn -- "every SELECT/UPDATE filtered<br/>by RLS USING (tenant_id = current_tenant)" --> Tables[(tenant-scoped tables)]
```

---

## Tech stack

| Layer | Choice |
|-------|--------|
| Language | Python 3.11+ |
| Framework | FastAPI (async) |
| ORM | SQLAlchemy 2.0 (async) |
| Validation | Pydantic v2 |
| Database | PostgreSQL 15+ (Row-Level Security) |
| Cache / broker | Redis 7+ |
| Background jobs | Celery |
| Migrations | Alembic |
| Auth | JWT RS256 (access 30m + refresh 7d) · CSRF double-submit |
| Logging | structlog (JSON in prod, console in dev) |
| Admin panel | SQLAdmin (session-cookie auth) |
| PDF | WeasyPrint + Jinja2 (Noto Sans Arabic) |
| Encryption | AES-256-GCM (merchant credentials at rest) |

---

## Quick start

**Prerequisites:** Python 3.11+, PostgreSQL 15+, Redis 7+.

```bash
# 1. Clone and install dependencies
git clone <repository-url>
cd NUMU-api
pip install -e ".[dev]"

# 2. Configure environment
cp .env.example .env

# 3. Start dependencies via Docker
docker compose -f docker/docker-compose.yml up -d db redis

# 4. Run migrations
alembic upgrade head

# 5. (optional) Seed sample data
python scripts/seed_data.py

# 6. Start the dev server
make dev   # or: uvicorn src.main:app --reload --port 8000
```

Full stack via Docker Compose:

```bash
docker compose -f docker/docker-compose.yml up --build
```

---

## API docs

Once running, the auto-generated docs live at:

| URL | UI |
|-----|-----|
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |
| `http://localhost:8000/admin` | SQLAdmin web panel (session-cookie auth) |

Routes are grouped under `/api/v1/`:

| Prefix | Domain |
|--------|--------|
| `/auth/` | Register · login · refresh · logout · CSRF · 2FA · email verification · password reset |
| `/stores/` | Store CRUD (owner) |
| `/stores/{id}/…` | Commerce: products · variants · categories · orders (+drafts · import · returns · refunds) · customers · inventory (+levels · transfers) · locations · gift_cards · bundles · shipments · shipping (+zones) |
| `/stores/{id}/…` | Growth: coupons · promotions (offers v2) · marketing campaigns/audiences · email_templates · upsells · abandoned_checkouts · social · analytics (+realtime) · dashboard · ai |
| `/stores/{id}/…` | Content: menus · pages · settings · onboarding · invoices · payment_proofs · payments · reconciliation · apps · order_import |
| `/stores/{id}/…` | WhatsApp & omnichannel: whatsapp (+campaigns · chat · templates · opt_ins · scheduled_sends) · channels · threads · messages · capi |
| `/stores/{id}/…` | Themes V3: themes · theme_editor_v3 · theme_installations · theme_updates · customizer_undo |
| `/themes` + `/marketplace/` | Theme marketplace: upload/build · developer submissions · admin review · catalog · purchases · reviews · store install |
| `/storefront/store/{id}/` | Public catalog · search · reviews · customer auth · checkout (+session) · gift cards · shipping rates · locations · payment proofs · pay · tracking |
| `/storefront/me/` | Customer profile · addresses · cart · checkout · wishlist · returns · saved cards · data rights |
| `/storefront/…` | `store-by-subdomain/{subdomain}` · theme_resolution · meta_feed (Meta Commerce XML) |
| `/staff/` + `/roles` + `/permissions` | Staff invitations · sessions · access requests · policies · RBAC |
| `/admin/` | Super-admin: tenants, waitlist, feedback, dashboard, platform config |
| `/tenants/` · `/public/` | Tenant registration & subdomain check · waitlist · landing config |
| `/billing` · `/referrals` · `/risk` | Subscriptions · merchant referrals · trust-network risk |
| `/shopify/` | Shopify app surface (11 sub-routers) |
| `/oauth/meta` | Meta OAuth for business scopes |
| `/webhooks/` | paymob · fawry · instapay · kashier · moyasar · jt · bosta · mylerz · meta · whatsapp · fawaterak · resend |
| `/ws` · `/health` | WebSocket realtime (inbox) · liveness probe |

---

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DEBUG` | Enable debug mode | `false` |
| `DATABASE_URL` | PostgreSQL connection string | required |
| `REDIS_URL` | Redis connection string | required |
| `JWT_PRIVATE_KEY` | RSA private key (PEM) | required |
| `JWT_PUBLIC_KEY` | RSA public key (PEM) | required |
| `STRIPE_SECRET_KEY` | Stripe API key | optional |
| `RESEND_API_KEY` | Resend email API key | optional |
| `OPENAI_API_KEY` | OpenAI API key | optional |
| `R2_ACCESS_KEY_ID` | Cloudflare R2 access key | optional |
| `META_APP_ID` | Meta (Facebook / Instagram) App ID | optional |
| `META_APP_SECRET` | Meta App Secret | optional |
| `META_WEBHOOK_VERIFY_TOKEN` | Webhook verification token | optional |
| `META_GRAPH_API_VERSION` | Graph API version | `v21.0` |
| `META_LOGIN_CONFIG_ID` | Facebook Login Config ID | optional |
| `INBOX_REALTIME_ENABLED` | Enable WebSocket inbox | `true` |
| `NOMINATIM_URL` | Self-hosted Nominatim base URL | optional |
| `LOCATIONIQ_KEY` | LocationIQ key (used if Nominatim unset) | optional |

See `src/config/settings.py` for the complete list.

---

## External services

| Category | Services |
|----------|----------|
| **Payments** | Paymob (cards + wallets) · Fawry · InstaPay · Kashier · Moyasar · Tap · JT · Stripe · Cash on Delivery |
| **Shipping** | Bosta · MyLerz (Egyptian couriers) · Shippo |
| **Email / SMS** | Resend · Twilio |
| **Messaging** | WhatsApp Business API (templates · campaigns · omnichannel inbox) |
| **Marketing** | Meta — Pixel, Conversions API, OAuth, custom/lookalike audiences, Messenger/Instagram inbox |
| **Storage** | Cloudflare R2 / MinIO / AWS S3 |
| **AI** | OpenAI (product descriptions, insights) · Hugging Face Vision (payment-proof OCR) |
| **Tax / e-invoicing** | ETA (Egypt) · ZATCA (Saudi Arabia) · Fawaterak |
| **Maps** | Self-hosted Nominatim *(or)* LocationIQ |
| **Monitoring** | Sentry · Slack (webhook channels, batched every 30s) |

### Self-hosted Nominatim

The storefront checkout location picker calls `/storefront/.../geocode/reverse`, which proxies to either a self-hosted [Nominatim](https://nominatim.org/) container or [LocationIQ](https://locationiq.com/) depending on env config.

```bash
docker compose --profile geocoding up nominatim
```

The first run downloads ~200 MB and imports the Egypt OSM extract (~5 GB on disk, ~30 min). Subsequent restarts come up in seconds. The container runs `UPDATE_MODE=continuous` against Geofabrik's daily Egypt diff feed — no Celery task or manual re-import required.

---

## Background jobs

```mermaid
flowchart LR
  API[NUMU-api] -- enqueue --> R[(Redis broker)]
  R --> W[Celery workers<br/>~57 task modules]
  W --> Email[Resend email]
  W --> WA[WhatsApp Business]
  W --> Slack[Slack webhooks · batched 30s]
  W --> Store[(R2 / S3 / MinIO)]
  W --> Meta[Meta CAPI]
```

| Area | Tasks (selection) |
|------|-------------------|
| Commerce | abandoned-cart recovery · back-in-stock · shipments · order webhooks |
| WhatsApp | campaign sends · scheduled dispatcher (60s) · template poll (15m) · dead-letter purge (daily) |
| Payments / risk | InstaPay expiry · COD deposit expiry · auto-RTO · risk scoring · fraud detection |
| Marketing | campaigns · promotions · Meta CAPI dispatch · social |
| Analytics | daily rollups · event ingest · retention purge · courier stats |
| Themes | theme build · upload · marketplace tasks |
| Lifecycle | demo cleanup · trial expiry · data retention · daily DB backup (03:00 UTC) · image processing |

---

## Project structure

<details>
<summary>Show tree</summary>

```text
NUMU-api/
├── alembic/                   # Database migrations
├── docker/                    # Docker configuration
├── docs/                      # Documentation
├── scripts/                   # Utility scripts (seed, ops, etc.)
├── src/
│   ├── api/
│   │   ├── dependencies/      # FastAPI Depends() factories
│   │   ├── middleware/        # CORS, logging, tenant, CSRF, etc.
│   │   ├── responses/         # Response envelopes
│   │   └── v1/
│   │       ├── routes/        # API endpoints grouped by domain
│   │       └── schemas/       # Pydantic request/response schemas
│   ├── application/
│   │   ├── dto/               # Data transfer objects
│   │   ├── services/          # Application services
│   │   └── use_cases/         # Business use cases
│   ├── config/                # Pydantic settings management
│   ├── core/
│   │   ├── entities/          # Domain entities
│   │   ├── exceptions/        # Domain exceptions
│   │   ├── interfaces/        # Repository + service interfaces
│   │   └── value_objects/     # Money, LocalizedString, etc.
│   └── infrastructure/
│       ├── cache/             # Redis cache
│       ├── database/          # SQLAlchemy setup + ORM models
│       ├── external_services/ # Paymob, Bosta, OpenAI, …
│       ├── messaging/         # Celery tasks
│       └── repositories/      # Repository implementations
└── tests/
    ├── unit/                  # Unit tests
    ├── integration/           # Integration tests (real DB / Redis)
    └── e2e/                   # End-to-end tests
```
</details>

---

## Testing

**Always invoke pytest through the project venv's interpreter**, never a bare
`pytest` off `PATH`. A globally-installed pytest imports whatever
FastAPI/Starlette pair sits in the user site-packages, and an out-of-range pair
kills `tests/conftest.py` at import time with
`TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`
(raised inside FastAPI's own `routing.py` when Starlette ≥1.0 is present). That
looks like an unrunnable test suite but is only a wrong interpreter — the venv
runs the same suite fine.

```bash
# Windows
.venv/Scripts/python.exe -m pytest                                   # full suite
.venv/Scripts/python.exe -m pytest --cov=src --cov-report=html       # with coverage
.venv/Scripts/python.exe -m pytest tests/unit/                       # unit only
.venv/Scripts/python.exe -m pytest tests/integration/                # integration only
.venv/Scripts/python.exe -m pytest tests/e2e/                        # end-to-end only

# macOS / Linux — same commands via .venv/bin/python
.venv/bin/python -m pytest tests/unit/
```

`make test` / `make test-cov` call a bare `pytest`, so they resolve to the venv
**only when it is already activated** (`.venv\Scripts\activate` /
`source .venv/bin/activate`) — that is deliberate, so the same targets work
inside the Docker image and CI, where there is no `.venv`. Activate first, or
use the explicit interpreter above.

Piping a full run through `tail`/`head` hides all progress until pytest exits
(the pipe block-buffers); redirect to a file instead if you want to watch it.

---

## Development tools

```bash
ruff format src/ tests/    # format
ruff check src/ tests/     # lint
mypy src/                  # type check
make migrate-new           # generate a new Alembic migration
make migrate               # apply migrations
make seed                  # seed sample data
```

---

## License

MIT.
