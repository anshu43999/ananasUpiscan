from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
LogLevel = Literal["info", "warn", "error"]
ReadyPlusChannel = Literal["upi", "kakao"]


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
    payment_method: Literal["upi", "ideal", "momo", "kakao", "card"] = "upi"
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


class ReadyPlusTaskSubmitItem(BaseModel):
    client_ref: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:-]+$")
    session_json: Any


class ReadyPlusTaskSubmitRequest(BaseModel):
    channel: ReadyPlusChannel = "upi"
    items: list[ReadyPlusTaskSubmitItem] = Field(min_length=1, max_length=20)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str:
        return str(value or "upi").strip().lower()


class ReadyPlusTaskSubmitResponse(BaseModel):
    ok: bool
    task_id: str | None = None
    status: str | None = None
    accepted: list[dict[str, Any]] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    balance: str | None = None
    idempotent_replay: bool = False


class ReadyPlusTaskDetailResponse(BaseModel):
    ok: bool
    task: dict[str, Any]


class ReadyPlusDownloadTokenResponse(BaseModel):
    ok: bool
    url: str
    expires_at: int


class MomoPermissionCheckRequest(BaseModel):
    access_token: str = Field(min_length=1)
    session_token: str | None = None
    proxy_seeds: list[str] = Field(default_factory=list)
    proxy_seed_chains: list[dict[str, str]] = Field(default_factory=list)
    capture_diagnostics: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class MomoPermissionCheckResponse(BaseModel):
    available: bool
    status: str
    payment_method_types: list[str] = Field(default_factory=list)
    local_methods: list[str] = Field(default_factory=list)
    amount: int | None = None
    currency: str | None = None
    checkout_id: str | None = None
    checkout_url: str | None = None
    error: str | None = None


class AccountEligibilityCheckRequest(BaseModel):
    token: str = Field(min_length=1)
    promo_id: str = "plus-1-month-free"


class AccountEligibilityCheckResponse(BaseModel):
    token_ok: bool = False
    eligible: bool = False
    reason: str | None = None
    coupon_state: str | None = None
    promo_id: str | None = None
    status: int | None = None
    email: str | None = None
    account_id: str | None = None
    plan_type: str | None = None
    phone_number: str | None = None
    phone_verified: bool | None = None
    reg_type: str | None = None
    jwt_expired: bool = False
    jwt_exp_ms: int | None = None
    jwt_exp_in_sec: int | None = None
    upi_eligible: bool | None = None
    upi_eligible_reason: str | None = None
    gcash_eligible: bool | None = None
    gcash_eligible_reason: str | None = None
    ideal_eligible: bool | None = None
    ideal_eligible_reason: str | None = None
    error: str | None = None


class AccountLibraryItem(BaseModel):
    id: int
    account_key: str
    account_id: str | None = None
    email: str | None = None
    plan_type: str | None = None
    status: str = "active"
    source: str | None = None
    channels: list[str] = Field(default_factory=list)
    eligibility_status: str = "unknown"
    eligibility_reason: str | None = None
    eligibility: dict[str, Any] = Field(default_factory=dict)
    last_checked_at: str | None = None
    health_status: str = "unknown"
    health_checked_at: str | None = None
    health_source: str | None = None
    health_error: str | None = None
    health: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    has_access_token: bool = False
    access_token_preview: str | None = None
    has_password: bool = False
    has_session_json: bool = False
    created_at: str
    updated_at: str


class AccountLibraryDetail(AccountLibraryItem):
    access_token: str | None = None
    password: str | None = None
    session_json: str | None = None


class AccountLibraryListResponse(BaseModel):
    ok: bool = True
    total: int = 0
    items: list[AccountLibraryItem] = Field(default_factory=list)


class AccountLibraryImportRequest(BaseModel):
    text: str = Field(min_length=1)
    default_channel: str = ""


class AccountLibraryImportResponse(BaseModel):
    ok: bool = True
    imported: int = 0
    items: list[AccountLibraryItem] = Field(default_factory=list)


class AccountLibraryUpdateRequest(BaseModel):
    email: str | None = None
    password: str | None = None
    access_token: str | None = None
    session_json: str | None = None
    status: str | None = None
    note: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> str | None:
        text = str(value or "").strip().lower()
        return text or None


class AccountLibraryIdsRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)


class AccountLibraryExportTokenRequest(AccountLibraryIdsRequest):
    only_eligible: bool = False


class AccountLibraryExportTokenResponse(BaseModel):
    ok: bool = True
    count: int = 0
    text: str = ""
    items: list[AccountLibraryItem] = Field(default_factory=list)


class AccountLibraryCheckRequest(AccountLibraryIdsRequest):
    promo_id: str = "plus-1-month-free"
    concurrency: int = Field(default=3, ge=1, le=5)


class AccountLibraryCheckResponse(BaseModel):
    ok: bool = True
    checked: int = 0
    items: list[AccountLibraryDetail] = Field(default_factory=list)


class AccountLibraryHealthRequest(AccountLibraryIdsRequest):
    concurrency: int = Field(default=8, ge=1, le=16)


class AccountLibraryHealthResponse(BaseModel):
    ok: bool = True
    checked: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    items: list[AccountLibraryDetail] = Field(default_factory=list)


class AccountLibraryExportJsonRequest(AccountLibraryIdsRequest):
    include_secrets: bool = False


class AccountLibraryExportJsonResponse(BaseModel):
    ok: bool = True
    count: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)
    text: str = ""


class AccountLibraryMutateResponse(BaseModel):
    ok: bool = True
    updated: int = 0
    deleted: int = 0


class AccountLibraryStatsResponse(BaseModel):
    ok: bool = True
    total: int = 0
    active: int = 0
    eligible: int = 0
    with_access_token: int = 0
    healthy: int = 0


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
