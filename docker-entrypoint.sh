#!/bin/sh
# Ensure the runtime data dir holds a populated SQLite DB before the API starts.
#
# The image is built with a seed database baked at /app/seed/arena.db (fresh
# public market data + an empty Hall of Fame). At runtime /app/data is often
# empty: a fresh bind-mount or a new named volume shadows the image's own
# /app/data. So seed it from the baked snapshot on first boot. If no seed exists
# (defensive — should not happen in a normally built image), fall back to a live
# fetch so the app is never left permanently empty ("bake-in + boot fallback").
set -e

DB=/app/data/arena.db
SEED=/app/seed/arena.db

if [ ! -f "$DB" ]; then
    mkdir -p /app/data
    if [ -f "$SEED" ]; then
        echo "[entrypoint] seeding $DB from baked snapshot ($SEED)"
        cp "$SEED" "$DB"
    else
        echo "[entrypoint] no baked seed found — fetching fresh data (needs network)…"
        python -m backend.data.refresh \
            || echo "[entrypoint] WARN: refresh failed; starting empty (use POST /api/admin/refresh)"
    fi
fi

exec "$@"
