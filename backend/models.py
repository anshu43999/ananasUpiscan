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
    payment_method: Literal["upi", "ideal", "momo"] = "upi"
    payment_page_mode: str = "custom"
    language: str = "auto"
    billing_country: str = "IN"
    proxy_chain: dict[str, str] | None = None
    proxy_seeds: list[str] = Field(default_factory=list)
    proxy_seed_chains: list[dict[str, str]] = Field(default_factory=list)
    custom_export_proxy: str | None = None
    client_fingerprint: str | None = None
    capture_diagnostics: bool = False
    cdk_code: str | None = None
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


class ExtractSettings(BaseModel):
    payment_methods: list[str] = Field(default_factory=lambda: ["upi", "ideal", "momo"])
    languages: list[str] = Field(default_factory=lambda: ["auto", "en", "zh"])
    billing_countries: list[str] = Field(default_factory=lambda: ["IN", "US", "VN", "NL"])
    proxy_regions: list[str] = Field(default_factory=lambda: ["IN", "US", "VN", "NL", "BR", "JP"])


class ExtractConfig(BaseModel):
    cdk_enabled: bool = False
    cost_per_task: int = 0
    log_visible: bool = True


class ProxyChainTestResult(BaseModel):
    success: bool
    latency_ms: int | None = None
    error: str | None = None


class PublisherSubmitCheckoutRequest(BaseModel):
    api_key: str = Field(min_length=1)
    api_base: str = "https://foarge.com/api/publisher/v1"
    task_id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    pay_link: str = Field(min_length=1)


class PublisherSubmitCheckoutResult(BaseModel):
    success: bool
    status_code: int
    message: str | None = None
    data: dict[str, Any] | None = None
