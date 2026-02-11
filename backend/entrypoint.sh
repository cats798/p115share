#!/bin/sh

# Exit immediately if a command exits with a non-zero status
set -e

echo "Running database migrations..."

# Check if alembic_version table exists (i.e., DB was previously managed by alembic)
# If the DB already exists but has no alembic_version table, stamp it to head
# so migrations don't try to re-create existing tables
if [ -f "data/p115share.db" ]; then
    # DB file exists - check if alembic_version table exists
    HAS_ALEMBIC=$(python -c "
import sqlite3
try:
    conn = sqlite3.connect('data/p115share.db')
    conn.execute('SELECT 1 FROM alembic_version LIMIT 1')
    print('yes')
except:
    print('no')
finally:
    conn.close()
" 2>/dev/null)
    
    if [ "$HAS_ALEMBIC" = "no" ]; then
        echo "Existing database found without migration tracking. Stamping as current..."
        alembic stamp head
    else
        echo "Running pending migrations..."
        alembic upgrade head
    fi
else
    echo "Fresh database, running all migrations..."
    alembic upgrade head
fi

echo "Starting application..."
exec python -m app.main
