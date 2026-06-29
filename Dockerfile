# Crypto Cycle Strategy Arena — single image serving API + static frontend,
# shipped with a baked seed database so a pulled image runs with data out of the
# box (no manual setup). Published to GHCR by .github/workflows/docker-publish.yml.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY backend/ backend/
COPY frontend/ frontend/

# Bake a seed database into the image: pull fresh public market data at build
# time and move it to /app/seed so it is NOT shadowed by a runtime volume mounted
# at /app/data. The seed contains only public market data (prices, indicators,
# Fear & Greed, MVRV) and an EMPTY runs ledger — no personal data. A flaky
# optional feed (e.g. an MVRV rate-limit) is tolerated, but the build fails if no
# price data was fetched. The bulky raw-snapshot cache is discarded (regenerable).
RUN python -m backend.data.refresh || true \
 && python -c "from backend.data.store import Store; s=Store().status(); import sys; ok=bool(s.get('prices')); print('[build] seed assets:', list(s.get('prices', {}).keys()) or 'NONE'); sys.exit(0 if ok else 1)" \
 && mkdir -p /app/seed \
 && mv /app/data/arena.db /app/seed/arena.db \
 && rm -rf /app/data \
 && mkdir -p /app/data

# Seed-or-fetch on first boot, then run the server (see docker-entrypoint.sh).
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
