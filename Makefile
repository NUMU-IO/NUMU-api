.PHONY: help install dev lint format type-check test test-cov run migrate seed docker-up docker-down clean staging-deploy staging-stop staging-logs staging-status load-smoke load-test load-stress load-ui

# Default target
help:
	@echo "NUMU Backend - Available Commands"
	@echo "=================================="
	@echo ""
	@echo "Development:"
	@echo "  install      Install production dependencies"
	@echo "  dev          Install development dependencies"
	@echo "  run          Start development server"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint         Run linter (ruff)"
	@echo "  format       Format code (ruff)"
	@echo "  type-check   Run type checker (mypy)"
	@echo ""
	@echo "Testing:"
	@echo "  test         Run all tests"
	@echo "  test-cov     Run tests with coverage"
	@echo "  test-obs     Run observability tests"
	@echo ""
	@echo "Load Testing:"
	@echo "  load-smoke   Smoke test  (10 users,  1 min)"
	@echo "  load-test    Load test   (100 users, 5 min)"
	@echo "  load-stress  Stress test (500 users, 10 min)"
	@echo "  load-ui      Open Locust web UI (port 8089)"
	@echo ""
	@echo "Database:"
	@echo "  migrate      Run database migrations"
	@echo "  migrate-new  Create new migration (use MSG=description)"
	@echo "  seed         Seed database with sample data"
	@echo ""
	@echo "Docker (Development):"
	@echo "  docker-up    Start all services with Docker"
	@echo "  docker-down  Stop all Docker services"
	@echo "  docker-build Build Docker image"
	@echo "  docker-logs  Follow Docker logs"
	@echo ""
	@echo "Staging Environment:"
	@echo "  staging-deploy   Deploy to staging"
	@echo "  staging-stop     Stop staging services"
	@echo "  staging-restart  Restart staging services"
	@echo "  staging-logs     Follow staging logs"
	@echo "  staging-status   Show staging service status"
	@echo "  staging-backup   Backup staging database"
	@echo "  staging-cleanup  Clean up staging resources"
	@echo ""
	@echo "Cleanup:"
	@echo "  clean        Remove cache and build artifacts"

# Installation
install:
	pip install -e .

dev:
	pip install -e ".[dev]"

# Development server
run:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Code quality
lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

type-check:
	mypy src/

# Testing
test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=src --cov-report=html --cov-report=term

test-obs:
	pytest tests/integration/test_observability.py -v

# Database
migrate:
	alembic upgrade head

migrate-new:
	alembic revision --autogenerate -m "$(MSG)"

migrate-down:
	alembic downgrade -1

seed:
	python scripts/seed_data.py

# Docker (Development)
docker-up:
	docker-compose -f docker/docker-compose.yml up -d

docker-down:
	docker-compose -f docker/docker-compose.yml down

docker-build:
	docker-compose -f docker/docker-compose.yml build

docker-logs:
	docker-compose -f docker/docker-compose.yml logs -f

# =============================================================================
# Load Testing (Locust)
# =============================================================================

LOCUST_FILE = tests/load/locustfile.py
LOCUST_HOST ?= http://localhost:8021
RESULTS_DIR = tests/load/results

_ensure_results_dir:
	@mkdir -p $(RESULTS_DIR)

load-smoke: _ensure_results_dir
	locust -f $(LOCUST_FILE) --host $(LOCUST_HOST) \
		--users 10 --spawn-rate 2 --run-time 1m --headless \
		--csv $(RESULTS_DIR)/smoke --html $(RESULTS_DIR)/smoke.html

load-test: _ensure_results_dir
	locust -f $(LOCUST_FILE) --host $(LOCUST_HOST) \
		--users 100 --spawn-rate 10 --run-time 5m --headless \
		--csv $(RESULTS_DIR)/load --html $(RESULTS_DIR)/load.html

load-stress: _ensure_results_dir
	locust -f $(LOCUST_FILE) --host $(LOCUST_HOST) \
		--users 500 --spawn-rate 25 --run-time 10m --headless \
		--csv $(RESULTS_DIR)/stress --html $(RESULTS_DIR)/stress.html

load-ui:
	locust -f $(LOCUST_FILE) --host $(LOCUST_HOST) --web-port 8089

# =============================================================================
# Staging Environment
# =============================================================================

staging-deploy:
	@chmod +x scripts/deploy_staging.sh
	@./scripts/deploy_staging.sh deploy

staging-stop:
	@chmod +x scripts/deploy_staging.sh
	@./scripts/deploy_staging.sh stop

staging-restart:
	@chmod +x scripts/deploy_staging.sh
	@./scripts/deploy_staging.sh restart

staging-logs:
	@chmod +x scripts/deploy_staging.sh
	@./scripts/deploy_staging.sh logs $(SVC)

staging-status:
	@chmod +x scripts/deploy_staging.sh
	@./scripts/deploy_staging.sh status

staging-backup:
	@chmod +x scripts/deploy_staging.sh
	@./scripts/deploy_staging.sh backup-db

staging-cleanup:
	@chmod +x scripts/deploy_staging.sh
	@./scripts/deploy_staging.sh cleanup

staging-rollback:
	@chmod +x scripts/deploy_staging.sh
	@./scripts/deploy_staging.sh rollback

# =============================================================================
# Cleanup
# =============================================================================
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name "htmlcov" -exec rm -rf {} +
	find . -type f -name ".coverage" -delete
	find . -type f -name "*.pyc" -delete

# Create Superuser
createsuperuser:
	python scripts/create_superuser.py

# Test Sentry integration
test-sentry:
	python -c "import sentry_sdk; from src.config import settings; sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment); sentry_sdk.capture_message('Test from NUMU API'); print('Test message sent to Sentry!')"
