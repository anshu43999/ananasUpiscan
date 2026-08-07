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


class AuthUser(BaseModel):
    id: int
    username: str
    role: str = "admin"
    status: str = "active"
    created_at: str = ""
    last_login_at: str = ""


class AuthStatusResponse(BaseModel):
    initialized: bool
    registration_open: bool


class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class AuthSessionResponse(BaseModel):
    ok: bool = True
    token: str
    user: AuthUser


class AuthMeResponse(BaseModel):
    ok: bool = True
    user: AuthUser


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
    plus_status: str = "unknown"
    plus_verified_at: str | None = None
    plus_check_source: str | None = None
    plus_check_error: str | None = None
    plus: dict[str, Any] = Field(default_factory=dict)
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


class AccountLibraryPlusVerifyRequest(AccountLibraryIdsRequest):
    concurrency: int = Field(default=8, ge=1, le=32)
    proxy_region: str = "JP"
    use_proxy_pool: bool = True
    go_email_protocol_url: str = ""


class AccountLibraryPlusVerifyResponse(BaseModel):
    ok: bool = True
    checked: int = 0
    paid: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    items: list[AccountLibraryDetail] = Field(default_factory=list)
    proxy_pool_used: bool = False
    proxy_region: str = "JP"


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
    plus: int = 0


class ResourcePoolItem(BaseModel):
    id: int
    resource_type: str
    provider: str
    resource_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    lease_id: str | None = None
    success_count: int = 0
    fail_count: int = 0
    cooldown_until: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str


class ResourcePoolListResponse(BaseModel):
    ok: bool = True
    items: list[ResourcePoolItem] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class ResourcePoolImportPhoneRequest(BaseModel):
    text: str = Field(min_length=1)
    provider: Literal["user_phone_url", "bind_user_phone_url"] = "user_phone_url"


class ResourcePoolImportProxyRequest(BaseModel):
    text: str = Field(min_length=1)
    provider: Literal["proxy_seed"] = "proxy_seed"
    protocol: Literal["socks5", "http", "https"] = "socks5"
    style: Literal["", "kookeey", "lajiao", "bestgo", "plain"] = ""


EmailProvider = Literal["icloud_api", "outlook_token", "icloud_privacy", "forwarded_domain", "cfworker_admin_api"]


class ResourcePoolImportEmailRequest(BaseModel):
    text: str = Field(min_length=1)
    provider: EmailProvider = "icloud_api"


class ResourcePoolImportResponse(BaseModel):
    ok: bool = True
    imported: int = 0
    total_rows: int = 0


class ResourcePoolStatusRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)
    status: Literal["available", "leased", "used", "cooldown", "disabled"] = "available"
    error: str = ""


class ResourcePoolMutateResponse(BaseModel):
    ok: bool = True
    updated: int = 0
    deleted: int = 0


class EmailRegistrationCreate(BaseModel):
    mailbox_text: str = ""
    mailbox_proxy: str = ""
    use_email_resource_pool: bool = False
    email_resource_provider: EmailProvider = "icloud_api"
    email_resource_count: int = Field(default=1, ge=1, le=500)
    registration_proxy: str = ""
    registration_proxies: str | list[str] = ""
    use_proxy_resource_pool: bool = False
    proxy_resource_provider: str = "proxy_seed"
    proxy_resource_count: int = Field(default=0, ge=0, le=500)
    proxy_seed_region: str = "JP"
    proxy_seed_ttl: int = Field(default=10, ge=1, le=1440)
    proxy_seed_protocol: Literal["socks5", "http", "https"] = "socks5"
    proxy_precheck_enabled: bool = True
    proxy_precheck_timeout: int = Field(default=12, ge=2, le=60)
    proxy_precheck_max_candidates: int = Field(default=100, ge=1, le=500)
    proxy_precheck_max_fraud_score: int = Field(default=50, ge=0, le=100)
    registration_retry_attempts: int = Field(default=2, ge=1, le=5)
    concurrency: int = Field(default=1, ge=1, le=8)
    headed: bool = False
    chatgpt_password: str = ""
    email_register_flow: str = "fast"
    email_protocol_backend: Literal["python", "go"] = "python"
    go_email_protocol_url: str = ""
    go_email_protocol_timeout_seconds: int = Field(default=900, ge=120, le=3600)
    go_email_protocol_poll_interval_ms: int = Field(default=1000, ge=500, le=10000)
    browser_engine: str = "playwright"
    email_otp_timeout: int = Field(default=200, ge=30, le=1200)
    email_otp_poll_interval: int = Field(default=3, ge=1, le=30)
    config: dict[str, Any] = Field(default_factory=dict)


class EmailRegistrationJobCreated(BaseModel):
    job_id: str


class EmailRegistrationLog(BaseModel):
    timestamp: str
    message: str
    level: LogLevel = "info"


class EmailRegistrationItem(BaseModel):
    ok: bool
    email: str
    account_id: str | None = None
    account: AccountLibraryItem | None = None
    proxy_label: str | None = None
    attempts: int | None = None
    tried_proxy_labels: list[str] = Field(default_factory=list)
    error: str | None = None


class EmailRegistrationSnapshot(BaseModel):
    job_id: str
    status: JobStatus
    total: int = 0
    completed: int = 0
    success: int = 0
    failed: int = 0
    logs: list[EmailRegistrationLog] = Field(default_factory=list)
    items: list[EmailRegistrationItem] = Field(default_factory=list)
    error: str | None = None
    created_at: str
    updated_at: str


class GoEmailBatchCreate(BaseModel):
    count: int = Field(default=1, ge=1, le=5000)
    max_concurrent: int = Field(default=0, ge=0, le=5000)
    batch_id: str = ""
    go_email_protocol_url: str = ""
    mailbox_provider: EmailProvider = "outlook_token"
    proxy_seed_region: str = "JP,US,DE,GB,BR"
    proxy_seed_styles: str = "bestgo,1024"
    proxy_seed_ttl: int = Field(default=15, ge=1, le=1440)
    email_otp_timeout: int = Field(default=120, ge=60, le=240)
    go_batch_timeout_seconds: int = Field(default=210, ge=120, le=1800)
    email_tries: int = Field(default=5, ge=1, le=20)
    skip_phone: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class GoEmailBatchResponse(BaseModel):
    ok: bool = True
    batch_id: str = ""
    snapshot: dict[str, Any] = Field(default_factory=dict)


class PhoneRegistrationCreate(BaseModel):
    phone_text: str = ""
    sms_provider: str = "user_phone_url"
    use_resource_pool: bool = False
    resource_provider: str = "user_phone_url"
    provider_count: int = Field(default=1, ge=1, le=100)
    sms_proxy: str = ""
    sms_api_key: str = ""
    sms_service: str = "dr"
    sms_country: str = ""
    sms_activate_api_key: str = ""
    sms_activate_country: str = ""
    herosms_api_key: str = ""
    herosms_service: str = ""
    herosms_country: str = ""
    herosms_max_price: float | None = None
    register_reuse_phone_to_max: bool = True
    register_phone_success_max: int = Field(default=3, ge=0, le=20)
    smsbower_api_key: str = ""
    smsbower_service: str = ""
    smsbower_country: str = ""
    smsbower_max_price: float | None = None
    smsbower_min_price: float | None = None
    smsbower_provider_ids: str = ""
    registration_proxy: str = ""
    registration_proxies: str | list[str] = ""
    use_proxy_resource_pool: bool = False
    proxy_resource_provider: str = "proxy_seed"
    proxy_resource_count: int = Field(default=0, ge=0, le=500)
    proxy_seed_region: str = "JP"
    proxy_seed_ttl: int = Field(default=10, ge=1, le=1440)
    proxy_seed_protocol: Literal["socks5", "http", "https"] = "socks5"
    proxy_precheck_enabled: bool = True
    proxy_precheck_timeout: int = Field(default=12, ge=2, le=60)
    proxy_precheck_max_candidates: int = Field(default=100, ge=1, le=500)
    proxy_precheck_max_fraud_score: int = Field(default=50, ge=0, le=100)
    registration_retry_attempts: int = Field(default=2, ge=1, le=5)
    concurrency: int = Field(default=1, ge=1, le=8)
    headed: bool = False
    chatgpt_password: str = ""
    browser_engine: str = "playwright"
    country_code: str = "1"
    country_name: str = "United States"
    sms_timeout: int = Field(default=180, ge=30, le=1200)
    sms_poll_interval: int = Field(default=3, ge=1, le=30)
    config: dict[str, Any] = Field(default_factory=dict)


class PhoneRegistrationJobCreated(BaseModel):
    job_id: str


class PhoneRegistrationItem(BaseModel):
    ok: bool
    phone: str
    email: str | None = None
    account_id: str | None = None
    account: AccountLibraryItem | None = None
    proxy_label: str | None = None
    attempts: int | None = None
    tried_proxy_labels: list[str] = Field(default_factory=list)
    error: str | None = None


class PhoneRegistrationSnapshot(BaseModel):
    job_id: str
    status: JobStatus
    total: int = 0
    completed: int = 0
    success: int = 0
    failed: int = 0
    logs: list[EmailRegistrationLog] = Field(default_factory=list)
    items: list[PhoneRegistrationItem] = Field(default_factory=list)
    error: str | None = None
    created_at: str
    updated_at: str


class OAuthResumeCreate(BaseModel):
    account_id: int | None = None
    account_ids: list[int] = Field(default_factory=list)
    resume_json: str = ""
    bind_email: str = ""
    bind_email_text: str = ""
    mailbox_proxy: str = ""
    bind_email_use_resource_pool: bool = False
    bind_email_resource_provider: EmailProvider = "icloud_api"
    bind_sms_provider: str = ""
    bind_use_resource_pool: bool = False
    bind_resource_provider: str = "bind_user_phone_url"
    bind_sms_phone_url: str = ""
    bind_sms_phone_urls: str = ""
    bind_sms_phone_url_file: str = ""
    bind_sms_proxy: str = ""
    bind_sms_api_key: str = ""
    bind_sms_service: str = "dr"
    bind_sms_country: str = ""
    bind_country_code: str = ""
    bind_country_name: str = ""
    bind_herosms_api_key: str = ""
    bind_herosms_service: str = ""
    bind_herosms_country: str = ""
    bind_herosms_max_price: float | None = None
    bind_smsbower_api_key: str = ""
    bind_smsbower_service: str = ""
    bind_smsbower_country: str = ""
    bind_smsbower_max_price: float | None = None
    bind_smsbower_min_price: float | None = None
    bind_smsbower_provider_ids: str = ""
    bind_sms_activate_api_key: str = ""
    bind_sms_activate_country: str = ""
    registration_proxy: str = ""
    registration_proxies: str | list[str] = ""
    use_proxy_resource_pool: bool = False
    proxy_resource_provider: str = "proxy_seed"
    proxy_resource_count: int = Field(default=0, ge=0, le=500)
    proxy_seed_region: str = "JP"
    proxy_seed_ttl: int = Field(default=10, ge=1, le=1440)
    proxy_seed_protocol: Literal["socks5", "http", "https"] = "socks5"
    proxy_precheck_enabled: bool = True
    proxy_precheck_timeout: int = Field(default=12, ge=2, le=60)
    proxy_precheck_max_candidates: int = Field(default=100, ge=1, le=500)
    proxy_precheck_max_fraud_score: int = Field(default=50, ge=0, le=100)
    registration_retry_attempts: int = Field(default=2, ge=1, le=5)
    concurrency: int = Field(default=1, ge=1, le=6)
    headed: bool = False
    chatgpt_password: str = ""
    browser_engine: str = "playwright"
    email_otp_timeout: int = Field(default=200, ge=30, le=1200)
    email_otp_poll_interval: int = Field(default=3, ge=1, le=30)
    allow_page_fallback: bool = True
    login_identity: str = ""
    redirect_uri: str = ""
    client_id: str = ""
    authorize_url: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class OAuthResumeJobCreated(BaseModel):
    job_id: str


class OAuthResumeItem(BaseModel):
    ok: bool
    email: str | None = None
    account_id: str | None = None
    account: AccountLibraryItem | None = None
    proxy_label: str | None = None
    attempts: int | None = None
    tried_proxy_labels: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class OAuthResumeSnapshot(BaseModel):
    job_id: str
    status: JobStatus
    total: int = 0
    completed: int = 0
    success: int = 0
    failed: int = 0
    logs: list[EmailRegistrationLog] = Field(default_factory=list)
    items: list[OAuthResumeItem] = Field(default_factory=list)
    error: str | None = None
    created_at: str
    updated_at: str


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
