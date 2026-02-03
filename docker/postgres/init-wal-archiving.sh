#!/bin/bash
# =============================================================================
# PostgreSQL WAL Archiving Initialization Script
# =============================================================================

set -e

# Create WAL archive directory
mkdir -p /var/lib/postgresql/wal_archive
chown postgres:postgres /var/lib/postgresql/wal_archive
chmod 700 /var/lib/postgresql/wal_archive

echo "WAL archiving directory initialized"

# Create a backup of pg_hba.conf and add replication permissions
cat >> "$PGDATA/pg_hba.conf" << EOF

# Replication connections
host    replication     all             172.28.0.0/16           scram-sha-256
EOF

echo "Replication permissions added to pg_hba.conf"
