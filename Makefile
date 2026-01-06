.PHONY: help install dev lint format type-check test test-cov run migrate seed docker-up docker-down clean

# Default target
help:
	@echo "Octyrafiy Backend - Available Commands"
	@echo "======================================="
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
	@echo ""
	@echo "Database:"
	@echo "  migrate      Run database migrations"
	@echo "  migrate-new  Create new migration (use MSG=description)"
	@echo "  seed         Seed database with sample data"
	@echo ""
	@echo "Docker:"
	@echo "  docker-up    Start all services with Docker"
	@echo "  docker-down  Stop all Docker services"
	@echo "  docker-build Build Docker image"
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

# Database
migrate:
	alembic upgrade head

migrate-new:
	alembic revision --autogenerate -m "$(MSG)"

migrate-down:
	alembic downgrade -1

seed:
	python scripts/seed_data.py

# Docker
docker-up:
	docker-compose -f docker/docker-compose.yml up -d

docker-down:
	docker-compose -f docker/docker-compose.yml down

docker-build:
	docker-compose -f docker/docker-compose.yml build

docker-logs:
	docker-compose -f docker/docker-compose.yml logs -f

# Cleanup
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name "htmlcov" -exec rm -rf {} +
	find . -type f -name ".coverage" -delete
	find . -type f -name "*.pyc" -delete
