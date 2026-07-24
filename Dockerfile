# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm install --no-audit --no-fund
COPY index.html tsconfig*.json vite.config.ts ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UPISCAN_WORKERS=2

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY --from=frontend /app/dist ./dist

RUN mkdir -p /app/data /app/logs /app/dumps \
    && touch /app/data/proxy_seeds.txt

ENV UPI_PROXY_SEED_FILE=/app/data/proxy_seeds.txt \
    UPI_PROXY_STATE_FILE=/app/data/proxy_state.json \
    UPI_LOG_DIR=/app/logs \
    UPI_DUMP_DIR=/app/dumps \
    UPISCAN_CORS_ORIGINS=*

EXPOSE 8000

CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
