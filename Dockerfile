# Crypto Cycle Strategy Arena — single image serving API + static frontend.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY backend/ backend/
COPY frontend/ frontend/

# Data dir (SQLite + raw cache) — mounted as a volume at runtime so it persists.
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
