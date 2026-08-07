# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm install --no-audit --no-fund
COPY index.html tsconfig*.json vite.config.ts ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM golang:1.22-bookworm AS go-email-worker-builder
WORKDIR /src/go-email-protocol
COPY go-email-protocol/go.mod go-email-protocol/go.sum ./
RUN go mod download
COPY go-email-protocol ./
RUN CGO_ENABLED=0 GOOS=linux go build -o /out/email-protocol-worker ./cmd/email-protocol-worker

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
RUN python -m playwright install --with-deps chromium

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

FROM debian:bookworm-slim AS go-email-worker
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=go-email-worker-builder /out/email-protocol-worker /usr/local/bin/email-protocol-worker

RUN mkdir -p /app/data

EXPOSE 18765

CMD ["email-protocol-worker", "-addr", "0.0.0.0:18765", "-db", "/app/data/email-protocol-ledger.db", "-key", "/app/data/email-protocol.key", "-business-db", "/app/data/upiscan.sqlite3", "-pure-go", "-protocol-mode=live", "-transport=direct", "-skip-sdk-drift"]
