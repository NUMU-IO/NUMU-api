# NUMU Backend

A modern e-commerce platform API built with FastAPI following Clean Architecture principles.

## 🏗️ Architecture

This project follows **Clean Architecture** with strict layer separation:

```
src/
├── api/           → Presentation layer (routes, schemas, middleware)
├── application/   → Use cases & DTOs (business logic orchestration)
├── core/          → Domain layer (entities, interfaces, value objects)
├── infrastructure/→ External concerns (DB, APIs, cache, messaging)
└── config/        → Application configuration
```

### Layer Dependencies
- **core/** → No dependencies on other layers (pure domain)
- **application/** → Depends only on `core/`
- **infrastructure/** → Implements interfaces from `core/interfaces/`
- **api/** → Orchestrates `application/` use cases via dependency injection

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+
- Redis 7+

### Development Setup

1. **Clone and install dependencies:**
   ```bash
   git clone <repository-url>
   cd octyrafiy-backend
   pip install -e ".[dev]"
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Start services with Docker:**
   ```bash
   docker-compose -f docker/docker-compose.yml up -d db redis
   ```

4. **Run migrations:**
   ```bash
   alembic upgrade head
   ```

5. **Seed the database (optional):**
   ```bash
   python scripts/seed_data.py
   ```

6. **Start the development server:**
   ```bash
   uvicorn src.main:app --reload
   ```

### Using Docker Compose (Full Stack)

```bash
docker-compose -f docker/docker-compose.yml up --build
```

## 📚 API Documentation

Once running, access the interactive API docs:
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

## 🗄️ Database Migrations

```bash
# Create a new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test types
pytest tests/unit/
pytest tests/integration/
pytest tests/e2e/
```

## 🛠️ Development Tools

```bash
# Format code
ruff format src/ tests/

# Lint code
ruff check src/ tests/

# Type checking
mypy src/
```

## 📁 Project Structure

```
├── alembic/                 # Database migrations
├── docker/                  # Docker configuration
├── scripts/                 # Utility scripts
├── src/
│   ├── api/
│   │   ├── dependencies/    # Dependency injection
│   │   ├── middleware/      # CORS, logging, errors
│   │   ├── responses/       # Response wrappers
│   │   └── v1/
│   │       ├── routes/      # API endpoints
│   │       └── schemas/     # Pydantic schemas
│   ├── application/
│   │   ├── dto/             # Data transfer objects
│   │   ├── services/        # Application services
│   │   └── use_cases/       # Business use cases
│   ├── config/              # Settings management
│   ├── core/
│   │   ├── entities/        # Domain entities
│   │   ├── exceptions/      # Domain exceptions
│   │   ├── interfaces/      # Repository & service interfaces
│   │   └── value_objects/   # Immutable value types
│   └── infrastructure/
│       ├── cache/           # Redis cache
│       ├── database/        # SQLAlchemy setup & models
│       ├── external_services/  # Third-party integrations
│       ├── messaging/       # Background tasks (Celery)
│       └── repositories/    # Repository implementations
└── tests/
    ├── e2e/                 # End-to-end tests
    ├── integration/         # Integration tests
    └── unit/                # Unit tests
```

## 🔧 Configuration

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `DEBUG` | Enable debug mode | `false` |
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `REDIS_URL` | Redis connection string | Required |
| `JWT_SECRET_KEY` | JWT signing secret | Required |
| `STRIPE_SECRET_KEY` | Stripe API key | Optional |
| `RESEND_API_KEY` | Resend email API key | Optional |
| `OPENAI_API_KEY` | OpenAI API key | Optional |
| `R2_ACCESS_KEY_ID` | Cloudflare R2 access key | Optional |

See `src/config/settings.py` for all configuration options.

## 🔌 External Services

| Service | Purpose |
|---------|---------|
| Stripe | Payment processing |
| Tap | Alternative payment gateway |
| Shippo | Shipping & logistics |
| OpenAI | AI-powered features |
| Resend | Email delivery |
| Cloudflare R2 | Object storage |

## 📝 License

MIT License
