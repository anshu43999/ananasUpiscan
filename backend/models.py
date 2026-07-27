from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
LogLevel = Literal["info", "warn", "error"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExtractJobLog(BaseModel):
    timestamp: datetime = Field(default_factory=utc_now)
    message: str
    level: LogLevel = "info"


class ExtractJobResult(BaseModel):
    url: str
    cs_id: str | None = None
    billing_country: str | None = None
    currency: str | None = None
    amount: int | None = None
    qr_code: str | None = None
    expires_at: datetime | None = None
    status: str = "ok"
    luck: int | None = None


class ExtractJobCreate(BaseModel):
    access_token: str = Field(min_length=1)
    session_token: str | None = None
    payment_method: Literal["upi", "ideal", "momo", "kakao"] = "upi"
    billing_country: str = "IN"
    proxy_seeds: list[str] = Field(default_factory=list)
    proxy_seed_chains: list[dict[str, str]] = Field(default_factory=list)
    capture_diagnostics: bool = False
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payment_method", mode="before")
    @classmethod
    def normalize_payment_method(cls, value: Any) -> str:
        return str(value or "upi").strip().lower()


class ExtractJobCreated(BaseModel):
    job_id: str


class ExtractJobSnapshot(BaseModel):
    job_id: str
    status: JobStatus
    logs: list[ExtractJobLog] = Field(default_factory=list)
    result: ExtractJobResult | None = None
    error: str | None = None
    diagnostic_url: str | None = None
    created_at: datetime
    updated_at: datetime


class ProxyChainTestResult(BaseModel):
    success: bool
    latency_ms: int | None = None
    error: str | None = None


class ProxyCheckRequest(BaseModel):
    proxies: str = Field(min_length=1)
    protocol: Literal["http", "socks5", "socks5h"] = "http"
    concurrency: int = Field(default=20, ge=1, le=100)
    timeout_ms: int = Field(default=10000, ge=1000, le=60000)


class ProxyCheckItem(BaseModel):
    id: int
    raw: str
    proxy: str
    ok: bool
    ip: str | None = None
    status: str
    latency_ms: int | None = None
    error: str | None = None


class ProxyCheckResponse(BaseModel):
    items: list[ProxyCheckItem] = Field(default_factory=list)
    total: int = 0
    ok: int = 0
    failed: int = 0
