#!/bin/bash
# =============================================================================
# NUMU API - Staging Deployment Script
# =============================================================================
# Usage: ./scripts/deploy_staging.sh [command]
# Commands: deploy, rollback, status, logs, backup-db, restore-db

set -e

# Configuration
COMPOSE_FILE="docker/docker-compose.staging.yml"
PROJECT_NAME="numu-staging"
BACKUP_DIR="./backups"
MAX_BACKUPS=5

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose is not installed"
        exit 1
    fi

    if [ ! -f ".env.staging" ]; then
        log_error ".env.staging file not found. Please create it from .env.staging template"
        exit 1
    fi

    if [ ! -f "docker/nginx/ssl/fullchain.pem" ] || [ ! -f "docker/nginx/ssl/privkey.pem" ]; then
        log_warning "SSL certificates not found. Generating self-signed certificates..."
        generate_ssl_certs
    fi

    log_success "Prerequisites check passed"
}

# Generate self-signed SSL certificates
generate_ssl_certs() {
    mkdir -p docker/nginx/ssl
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout docker/nginx/ssl/privkey.pem \
        -out docker/nginx/ssl/fullchain.pem \
        -subj "/CN=staging.numu.com" 2>/dev/null
    log_success "Self-signed SSL certificates generated"
}

# Build and deploy
deploy() {
    log_info "Starting deployment to staging..."

    check_prerequisites

    # Pull latest changes
    log_info "Pulling latest changes from git..."
    git pull origin dev || log_warning "Git pull failed, continuing with local changes"

    # Build images
    log_info "Building Docker images..."
    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME build --no-cache

    # Stop existing containers
    log_info "Stopping existing containers..."
    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME down --remove-orphans || true

    # Start services
    log_info "Starting services..."
    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME up -d

    # Wait for services to be healthy
    log_info "Waiting for services to be healthy..."
    sleep 10

    # Run database migrations
    log_info "Running database migrations..."
    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME exec -T api alembic upgrade head || {
        log_error "Migration failed!"
        rollback
        exit 1
    }

    # Health check
    log_info "Performing health check..."
    sleep 5
    if curl -sf http://localhost/api/v1/public/health > /dev/null; then
        log_success "Health check passed!"
    else
        log_warning "Health check failed, but services may still be starting..."
    fi

    # Show status
    status

    log_success "Deployment completed successfully!"
}

# Rollback to previous version
rollback() {
    log_info "Rolling back to previous version..."

    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME down --remove-orphans || true

    # Restore from latest backup if available
    LATEST_BACKUP=$(ls -t $BACKUP_DIR/*.sql 2>/dev/null | head -1)
    if [ -n "$LATEST_BACKUP" ]; then
        log_info "Found backup: $LATEST_BACKUP"
        log_warning "To restore database, run: ./scripts/deploy_staging.sh restore-db $LATEST_BACKUP"
    fi

    log_success "Rollback completed"
}

# Show status
status() {
    log_info "Service Status:"
    echo ""
    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME ps
    echo ""

    log_info "Resource Usage:"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" $(docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME ps -q) 2>/dev/null || true
}

# Show logs
logs() {
    SERVICE=${1:-""}
    if [ -n "$SERVICE" ]; then
        docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME logs -f $SERVICE
    else
        docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME logs -f
    fi
}

# Backup database
backup_db() {
    log_info "Creating database backup..."

    mkdir -p $BACKUP_DIR

    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_FILE="$BACKUP_DIR/numu_staging_$TIMESTAMP.sql"

    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME exec -T postgres \
        pg_dump -U numu numu_staging > $BACKUP_FILE

    # Compress backup
    gzip $BACKUP_FILE
    BACKUP_FILE="${BACKUP_FILE}.gz"

    log_success "Backup created: $BACKUP_FILE"

    # Cleanup old backups
    log_info "Cleaning up old backups (keeping last $MAX_BACKUPS)..."
    ls -t $BACKUP_DIR/*.sql.gz 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs rm -f 2>/dev/null || true

    log_success "Database backup completed"
}

# Restore database
restore_db() {
    BACKUP_FILE=$1

    if [ -z "$BACKUP_FILE" ]; then
        log_error "Please specify backup file: ./scripts/deploy_staging.sh restore-db <backup_file>"
        exit 1
    fi

    if [ ! -f "$BACKUP_FILE" ]; then
        log_error "Backup file not found: $BACKUP_FILE"
        exit 1
    fi

    log_warning "This will overwrite the current database. Are you sure? (y/N)"
    read -r confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        log_info "Restore cancelled"
        exit 0
    fi

    log_info "Restoring database from $BACKUP_FILE..."

    # Decompress if needed
    if [[ $BACKUP_FILE == *.gz ]]; then
        gunzip -k $BACKUP_FILE
        BACKUP_FILE="${BACKUP_FILE%.gz}"
    fi

    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME exec -T postgres \
        psql -U numu -d numu_staging < $BACKUP_FILE

    log_success "Database restored from $BACKUP_FILE"
}

# Stop services
stop() {
    log_info "Stopping staging services..."
    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME down
    log_success "Services stopped"
}

# Restart services
restart() {
    log_info "Restarting staging services..."
    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME restart
    log_success "Services restarted"
}

# Clean up
cleanup() {
    log_info "Cleaning up Docker resources..."
    docker-compose -f $COMPOSE_FILE -p $PROJECT_NAME down -v --remove-orphans
    docker system prune -f
    log_success "Cleanup completed"
}

# Main
case "${1:-deploy}" in
    deploy)
        deploy
        ;;
    rollback)
        rollback
        ;;
    status)
        status
        ;;
    logs)
        logs $2
        ;;
    backup-db)
        backup_db
        ;;
    restore-db)
        restore_db $2
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    cleanup)
        cleanup
        ;;
    *)
        echo "Usage: $0 {deploy|rollback|status|logs|backup-db|restore-db|stop|restart|cleanup}"
        echo ""
        echo "Commands:"
        echo "  deploy      Build and deploy to staging"
        echo "  rollback    Rollback to previous version"
        echo "  status      Show service status"
        echo "  logs [svc]  Show logs (optionally for specific service)"
        echo "  backup-db   Create database backup"
        echo "  restore-db  Restore database from backup"
        echo "  stop        Stop all services"
        echo "  restart     Restart all services"
        echo "  cleanup     Remove all containers and volumes"
        exit 1
        ;;
esac
