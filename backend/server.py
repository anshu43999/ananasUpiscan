from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from .job_manager import JobManager
from .models import (
    AccountEligibilityCheckRequest,
    AccountEligibilityCheckResponse,
    AccountLibraryCheckRequest,
    AccountLibraryCheckResponse,
    AccountLibraryDetail,
    AccountLibraryExportTokenRequest,
    AccountLibraryExportTokenResponse,
    AccountLibraryExportJsonRequest,
    AccountLibraryExportJsonResponse,
    AccountLibraryHealthRequest,
    AccountLibraryHealthResponse,
    AccountLibraryIdsRequest,
    AccountLibraryImportRequest,
    AccountLibraryImportResponse,
    AccountLibraryListResponse,
    AccountLibraryMutateResponse,
    AccountLibraryPlusVerifyRequest,
    AccountLibraryPlusVerifyResponse,
    AccountLibraryStatsResponse,
    AccountLibraryUpdateRequest,
    EmailRegistrationCreate,
    EmailRegistrationJobCreated,
    EmailRegistrationSnapshot,
    ExtractJobCreate,
    ExtractJobCreated,
    ExtractJobSnapshot,
    GoEmailBatchCreate,
    GoEmailBatchResponse,
    MomoPermissionCheckRequest,
    MomoPermissionCheckResponse,
    OAuthResumeCreate,
    OAuthResumeJobCreated,
    OAuthResumeSnapshot,
    PhoneRegistrationCreate,
    PhoneRegistrationJobCreated,
    PhoneRegistrationSnapshot,
    ProxyCheckRequest,
    ProxyCheckResponse,
    ProxyChainTestResult,
    ReadyPlusDownloadTokenResponse,
    ReadyPlusTaskDetailResponse,
    ReadyPlusTaskSubmitRequest,
    ReadyPlusTaskSubmitResponse,
    ResourcePoolImportEmailRequest,
    ResourcePoolImportPhoneRequest,
    ResourcePoolImportProxyRequest,
    ResourcePoolImportResponse,
    ResourcePoolListResponse,
    ResourcePoolMutateResponse,
    ResourcePoolStatusRequest,
)
from . import account_library
from . import resource_pool
from .account_check import check_account_eligibility
from .email_registration import email_registration_manager
from .extractor.context import ExtractionContext
from .extractor.extract import config_from_env, load_token
from .extractor.vietnam import check_momo_permission
from .go_email_protocol import (
    cancel_go_registration_batch,
    get_go_registration_batch,
    start_go_registration_batch,
    worker_supports_batches,
)
from .oauth_resume import oauth_resume_manager
from .phone_registration import phone_registration_manager
from .proxy_check import check_proxies
from .ready_plus_client import ReadyPlusApiError, ready_plus_download, ready_plus_json
from .ws_manager import WebSocketManager


app = FastAPI(title="UPIScan Backend")
ws_manager = WebSocketManager()
job_manager = JobManager(ws_manager, max_workers=int(os.environ.get("UPISCAN_WORKERS", "2")))

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("UPISCAN_CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    await asyncio.to_thread(lambda: account_library.connect().close())
    await asyncio.to_thread(lambda: resource_pool.connect().close())
    await job_manager.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await job_manager.shutdown()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/extract/jobs", response_model=ExtractJobCreated)
async def create_extract_job(request: ExtractJobCreate) -> ExtractJobCreated:
    snapshot = await job_manager.create_job(request)
    return ExtractJobCreated(job_id=snapshot.job_id)


@app.get("/api/extract/jobs/{job_id}", response_model=ExtractJobSnapshot)
async def get_extract_job(job_id: str) -> ExtractJobSnapshot:
    snapshot = await job_manager.get_job(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="job not found")
    return snapshot


@app.post("/api/extract/jobs/{job_id}/cancel", response_model=ExtractJobSnapshot)
async def cancel_extract_job(job_id: str) -> ExtractJobSnapshot:
    snapshot = await job_manager.cancel_job(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="job not found")
    return snapshot


@app.post("/api/proxy-chain-test", response_model=ProxyChainTestResult)
async def proxy_chain_test() -> ProxyChainTestResult:
    return ProxyChainTestResult(success=True, latency_ms=0)


@app.post("/api/account-eligibility-check", response_model=AccountEligibilityCheckResponse)
async def account_eligibility_check(request: AccountEligibilityCheckRequest) -> AccountEligibilityCheckResponse:
    payload = await asyncio.to_thread(check_account_eligibility, request.token, request.promo_id)
    return AccountEligibilityCheckResponse(**payload)


@app.get("/api/accounts", response_model=AccountLibraryListResponse)
async def list_account_library(
    search: str = "",
    status: str = "active",
    eligibility: str = "",
    limit: int = Query(default=500, ge=1, le=2000),
) -> AccountLibraryListResponse:
    payload = await asyncio.to_thread(account_library.list_accounts, search, status, eligibility, limit)
    return AccountLibraryListResponse(**payload)


@app.get("/api/accounts/stats", response_model=AccountLibraryStatsResponse)
async def account_library_stats() -> AccountLibraryStatsResponse:
    payload = await asyncio.to_thread(account_library.stats)
    return AccountLibraryStatsResponse(**payload)


@app.post("/api/accounts/import", response_model=AccountLibraryImportResponse)
async def import_account_library(request: AccountLibraryImportRequest) -> AccountLibraryImportResponse:
    payload = await asyncio.to_thread(account_library.import_accounts, request.text, request.default_channel)
    return AccountLibraryImportResponse(**payload)


@app.get("/api/accounts/{account_id}", response_model=AccountLibraryDetail)
async def get_account_library_item(account_id: int) -> AccountLibraryDetail:
    payload = await asyncio.to_thread(account_library.get_account, account_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="account not found")
    return AccountLibraryDetail(**payload)


@app.put("/api/accounts/{account_id}", response_model=AccountLibraryDetail)
async def update_account_library_item(account_id: int, request: AccountLibraryUpdateRequest) -> AccountLibraryDetail:
    payload = await asyncio.to_thread(account_library.update_account, account_id, request.model_dump(exclude_unset=True))
    if payload is None:
        raise HTTPException(status_code=404, detail="account not found")
    detail = await asyncio.to_thread(account_library.get_account, account_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="account not found")
    return AccountLibraryDetail(**detail)


@app.post("/api/accounts/{account_id}/archive", response_model=AccountLibraryMutateResponse)
async def archive_account_library_item(account_id: int) -> AccountLibraryMutateResponse:
    payload = await asyncio.to_thread(account_library.set_status, [account_id], "archived")
    return AccountLibraryMutateResponse(**payload)


@app.post("/api/accounts/{account_id}/restore", response_model=AccountLibraryMutateResponse)
async def restore_account_library_item(account_id: int) -> AccountLibraryMutateResponse:
    payload = await asyncio.to_thread(account_library.set_status, [account_id], "active")
    return AccountLibraryMutateResponse(**payload)


@app.delete("/api/accounts/{account_id}", response_model=AccountLibraryMutateResponse)
async def delete_account_library_item(account_id: int) -> AccountLibraryMutateResponse:
    payload = await asyncio.to_thread(account_library.delete_accounts, [account_id])
    return AccountLibraryMutateResponse(**payload)


@app.post("/api/accounts-bulk/archive", response_model=AccountLibraryMutateResponse)
async def archive_account_library_items(request: AccountLibraryIdsRequest) -> AccountLibraryMutateResponse:
    payload = await asyncio.to_thread(account_library.set_status, request.ids, "archived")
    return AccountLibraryMutateResponse(**payload)


@app.post("/api/accounts-bulk/delete", response_model=AccountLibraryMutateResponse)
async def delete_account_library_items(request: AccountLibraryIdsRequest) -> AccountLibraryMutateResponse:
    payload = await asyncio.to_thread(account_library.delete_accounts, request.ids)
    return AccountLibraryMutateResponse(**payload)


@app.post("/api/accounts-bulk/export-tokens", response_model=AccountLibraryExportTokenResponse)
async def export_account_library_tokens(request: AccountLibraryExportTokenRequest) -> AccountLibraryExportTokenResponse:
    payload = await asyncio.to_thread(account_library.export_tokens, request.ids or None, only_eligible=request.only_eligible)
    return AccountLibraryExportTokenResponse(**payload)


@app.post("/api/accounts-bulk/export-import-text", response_model=AccountLibraryExportTokenResponse)
async def export_account_library_import_text(request: AccountLibraryIdsRequest) -> AccountLibraryExportTokenResponse:
    payload = await asyncio.to_thread(account_library.export_import_text, request.ids or None)
    return AccountLibraryExportTokenResponse(**payload)


@app.post("/api/accounts-bulk/check-eligibility", response_model=AccountLibraryCheckResponse)
async def check_account_library_items(request: AccountLibraryCheckRequest) -> AccountLibraryCheckResponse:
    payload = await asyncio.to_thread(account_library.check_accounts, request.ids, request.promo_id, request.concurrency)
    return AccountLibraryCheckResponse(**payload)


@app.post("/api/accounts-bulk/check-health", response_model=AccountLibraryHealthResponse)
async def check_account_library_health(request: AccountLibraryHealthRequest) -> AccountLibraryHealthResponse:
    payload = await asyncio.to_thread(account_library.check_health, request.ids, request.concurrency)
    return AccountLibraryHealthResponse(**payload)


@app.post("/api/accounts-bulk/verify-plus", response_model=AccountLibraryPlusVerifyResponse)
async def verify_account_library_plus(request: AccountLibraryPlusVerifyRequest) -> AccountLibraryPlusVerifyResponse:
    payload = await asyncio.to_thread(
        account_library.verify_plus,
        request.ids,
        concurrency=request.concurrency,
        proxy_region=request.proxy_region,
        use_proxy_pool=request.use_proxy_pool,
        go_email_protocol_url=request.go_email_protocol_url,
    )
    return AccountLibraryPlusVerifyResponse(**payload)


@app.post("/api/accounts-bulk/mark-plus", response_model=AccountLibraryMutateResponse)
async def mark_account_library_plus(request: AccountLibraryIdsRequest) -> AccountLibraryMutateResponse:
    payload = await asyncio.to_thread(account_library.mark_plus, request.ids)
    return AccountLibraryMutateResponse(**payload)


@app.post("/api/accounts-bulk/export-json", response_model=AccountLibraryExportJsonResponse)
async def export_account_library_json(request: AccountLibraryExportJsonRequest) -> AccountLibraryExportJsonResponse:
    payload = await asyncio.to_thread(account_library.export_json, request.ids or None, include_secrets=request.include_secrets)
    return AccountLibraryExportJsonResponse(**payload)


@app.get("/api/resources", response_model=ResourcePoolListResponse)
async def list_resource_pool(
    resource_type: str = "phone",
    provider: str = "",
    status: str = "all",
    limit: int = Query(default=2000, ge=1, le=5000),
) -> ResourcePoolListResponse:
    payload = await asyncio.to_thread(resource_pool.list_resources, resource_type, provider, status, limit)
    return ResourcePoolListResponse(**payload)


@app.post("/api/resources/import-phone", response_model=ResourcePoolImportResponse)
async def import_resource_pool_phone(request: ResourcePoolImportPhoneRequest) -> ResourcePoolImportResponse:
    payload = await asyncio.to_thread(resource_pool.import_phone_urls, request.text, request.provider)
    return ResourcePoolImportResponse(**payload)


@app.post("/api/resources/import-proxy-seeds", response_model=ResourcePoolImportResponse)
async def import_resource_pool_proxy_seeds(request: ResourcePoolImportProxyRequest) -> ResourcePoolImportResponse:
    payload = await asyncio.to_thread(
        resource_pool.import_proxy_seeds,
        request.text,
        provider=request.provider,
        protocol=request.protocol,
        style=request.style,
    )
    return ResourcePoolImportResponse(**payload)


@app.post("/api/resources/import-email", response_model=ResourcePoolImportResponse)
async def import_resource_pool_email(request: ResourcePoolImportEmailRequest) -> ResourcePoolImportResponse:
    payload = await asyncio.to_thread(
        resource_pool.import_email_resources,
        request.text,
        provider=request.provider,
    )
    return ResourcePoolImportResponse(**payload)


@app.post("/api/resources/status", response_model=ResourcePoolMutateResponse)
async def set_resource_pool_status(request: ResourcePoolStatusRequest) -> ResourcePoolMutateResponse:
    payload = await asyncio.to_thread(resource_pool.set_status_many, request.ids, status=request.status, error=request.error)
    return ResourcePoolMutateResponse(**payload)


@app.post("/api/resources/delete", response_model=ResourcePoolMutateResponse)
async def delete_resource_pool_items(request: AccountLibraryIdsRequest) -> ResourcePoolMutateResponse:
    payload = await asyncio.to_thread(resource_pool.delete_many, request.ids)
    return ResourcePoolMutateResponse(**payload)


@app.post("/api/email-registration/jobs", response_model=EmailRegistrationJobCreated)
async def create_email_registration_job(request: EmailRegistrationCreate) -> EmailRegistrationJobCreated:
    job = await asyncio.to_thread(email_registration_manager.create_job, request.model_dump())
    return EmailRegistrationJobCreated(job_id=job.job_id)


@app.get("/api/email-registration/jobs/{job_id}", response_model=EmailRegistrationSnapshot)
async def get_email_registration_job(job_id: str) -> EmailRegistrationSnapshot:
    job = await asyncio.to_thread(email_registration_manager.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="email registration job not found")
    return EmailRegistrationSnapshot(**job.to_dict())


def _go_email_batch_config(request: GoEmailBatchCreate | None = None, go_email_protocol_url: str = "") -> dict[str, object]:
    config: dict[str, object] = config_from_env(Path.cwd())
    if request is not None:
        config.update(request.config or {})
        config.update(
            {
                "go_email_protocol_url": request.go_email_protocol_url,
                "mailbox_provider": request.mailbox_provider,
                "proxy_seed_region": request.proxy_seed_region,
                "proxy_seed_styles": request.proxy_seed_styles,
                "proxy_seed_ttl": request.proxy_seed_ttl,
                "email_otp_timeout": request.email_otp_timeout,
                "go_batch_timeout_seconds": request.go_batch_timeout_seconds,
                "email_tries": request.email_tries,
                "mailat_protocol_skip_phone": request.skip_phone,
            }
        )
    elif go_email_protocol_url:
        config["go_email_protocol_url"] = go_email_protocol_url
    return config


@app.get("/api/go-email-batches/health")
async def go_email_batch_health(go_email_protocol_url: str = "") -> dict[str, object]:
    config = _go_email_batch_config(None, go_email_protocol_url)
    supported = await asyncio.to_thread(worker_supports_batches, config)
    return {"ok": True, "supported": supported}


@app.post("/api/go-email-batches", response_model=GoEmailBatchResponse)
async def create_go_email_batch(request: GoEmailBatchCreate) -> GoEmailBatchResponse:
    config = _go_email_batch_config(request)
    supported = await asyncio.to_thread(worker_supports_batches, config)
    if not supported:
        raise HTTPException(status_code=400, detail="Go worker does not support email-register-batches")
    max_concurrent = request.max_concurrent if request.max_concurrent > 0 else None
    try:
        snapshot = await asyncio.to_thread(
            start_go_registration_batch,
            count=request.count,
            config=config,
            batch_id=request.batch_id,
            max_concurrent=max_concurrent,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return GoEmailBatchResponse(batch_id=str(snapshot.get("batch_id") or request.batch_id or ""), snapshot=snapshot)


@app.get("/api/go-email-batches/{batch_id}", response_model=GoEmailBatchResponse)
async def get_go_email_batch(batch_id: str, go_email_protocol_url: str = "") -> GoEmailBatchResponse:
    config = _go_email_batch_config(None, go_email_protocol_url)
    try:
        snapshot = await asyncio.to_thread(get_go_registration_batch, batch_id, config)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return GoEmailBatchResponse(batch_id=str(snapshot.get("batch_id") or batch_id), snapshot=snapshot)


@app.delete("/api/go-email-batches/{batch_id}", response_model=GoEmailBatchResponse)
async def cancel_go_email_batch(batch_id: str, go_email_protocol_url: str = "") -> GoEmailBatchResponse:
    config = _go_email_batch_config(None, go_email_protocol_url)
    try:
        snapshot = await asyncio.to_thread(cancel_go_registration_batch, batch_id, config)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return GoEmailBatchResponse(batch_id=str(snapshot.get("batch_id") or batch_id), snapshot=snapshot)


@app.post("/api/phone-registration/jobs", response_model=PhoneRegistrationJobCreated)
async def create_phone_registration_job(request: PhoneRegistrationCreate) -> PhoneRegistrationJobCreated:
    job = await asyncio.to_thread(phone_registration_manager.create_job, request.model_dump())
    return PhoneRegistrationJobCreated(job_id=job.job_id)


@app.get("/api/phone-registration/jobs/{job_id}", response_model=PhoneRegistrationSnapshot)
async def get_phone_registration_job(job_id: str) -> PhoneRegistrationSnapshot:
    job = await asyncio.to_thread(phone_registration_manager.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="phone registration job not found")
    return PhoneRegistrationSnapshot(**job.to_dict())


@app.post("/api/oauth-resume/jobs", response_model=OAuthResumeJobCreated)
async def create_oauth_resume_job(request: OAuthResumeCreate) -> OAuthResumeJobCreated:
    job = await asyncio.to_thread(oauth_resume_manager.create_job, request.model_dump())
    return OAuthResumeJobCreated(job_id=job.job_id)


@app.get("/api/oauth-resume/jobs/{job_id}", response_model=OAuthResumeSnapshot)
async def get_oauth_resume_job(job_id: str) -> OAuthResumeSnapshot:
    job = await asyncio.to_thread(oauth_resume_manager.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="oauth resume job not found")
    return OAuthResumeSnapshot(**job.to_dict())


def _ready_plus_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ReadyPlusApiError):
        headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
        return HTTPException(status_code=exc.status_code, detail=exc.payload, headers=headers)
    return HTTPException(
        status_code=503,
        detail={"ok": False, "error": {"code": "ready_plus_unconfigured", "message": str(exc)}},
    )


@app.get("/api/ready-plus/me")
async def ready_plus_me(x_ready_plus_key: str | None = Header(default=None, alias="X-Ready-Plus-Key")) -> dict[str, object]:
    try:
        return await asyncio.to_thread(ready_plus_json, "GET", "/api/v1/me", api_key=x_ready_plus_key)
    except Exception as exc:
        raise _ready_plus_error(exc) from exc


@app.get("/api/ready-plus/balance")
async def ready_plus_balance(x_ready_plus_key: str | None = Header(default=None, alias="X-Ready-Plus-Key")) -> dict[str, object]:
    try:
        return await asyncio.to_thread(ready_plus_json, "GET", "/api/v1/balance", api_key=x_ready_plus_key)
    except Exception as exc:
        raise _ready_plus_error(exc) from exc


@app.get("/api/ready-plus/tasks")
async def ready_plus_tasks(
    limit: int = Query(default=20, ge=1, le=100),
    x_ready_plus_key: str | None = Header(default=None, alias="X-Ready-Plus-Key"),
) -> dict[str, object]:
    try:
        return await asyncio.to_thread(ready_plus_json, "GET", "/api/v1/tasks", params={"limit": limit}, api_key=x_ready_plus_key)
    except Exception as exc:
        raise _ready_plus_error(exc) from exc


@app.post("/api/ready-plus/tasks", response_model=ReadyPlusTaskSubmitResponse)
async def ready_plus_submit_task(
    request: ReadyPlusTaskSubmitRequest,
    x_ready_plus_key: str | None = Header(default=None, alias="X-Ready-Plus-Key"),
) -> ReadyPlusTaskSubmitResponse:
    idempotency_key = request.idempotency_key or f"upiscan-{uuid.uuid4().hex}"
    body = {
        "channel": request.channel,
        "items": [item.model_dump() for item in request.items],
    }
    try:
        payload = await asyncio.to_thread(
            ready_plus_json,
            "POST",
            "/api/v1/tasks",
            json_body=body,
            idempotency_key=idempotency_key,
            api_key=x_ready_plus_key,
        )
        return ReadyPlusTaskSubmitResponse(**payload)
    except ReadyPlusApiError as exc:
        if exc.status_code == 409 and isinstance(exc.payload, dict) and exc.payload.get("task_id"):
            return ReadyPlusTaskSubmitResponse(**exc.payload)
        raise _ready_plus_error(exc) from exc
    except Exception as exc:
        raise _ready_plus_error(exc) from exc


@app.get("/api/ready-plus/tasks/{task_id}", response_model=ReadyPlusTaskDetailResponse)
async def ready_plus_task(task_id: str, x_ready_plus_key: str | None = Header(default=None, alias="X-Ready-Plus-Key")) -> ReadyPlusTaskDetailResponse:
    try:
        payload = await asyncio.to_thread(ready_plus_json, "GET", f"/api/v1/tasks/{task_id}", api_key=x_ready_plus_key)
        return ReadyPlusTaskDetailResponse(**payload)
    except Exception as exc:
        raise _ready_plus_error(exc) from exc


@app.get("/api/ready-plus/items/{item_id}/download-token", response_model=ReadyPlusDownloadTokenResponse)
async def ready_plus_download_token(
    item_id: str,
    x_ready_plus_key: str | None = Header(default=None, alias="X-Ready-Plus-Key"),
) -> ReadyPlusDownloadTokenResponse:
    try:
        payload = await asyncio.to_thread(ready_plus_json, "GET", f"/api/v1/items/{item_id}/download-token", api_key=x_ready_plus_key)
        return ReadyPlusDownloadTokenResponse(**payload)
    except Exception as exc:
        raise _ready_plus_error(exc) from exc


@app.get("/api/ready-plus/items/{item_id}/download")
async def ready_plus_download_artifact(
    item_id: str,
    token: str,
    x_ready_plus_key: str | None = Header(default=None, alias="X-Ready-Plus-Key"),
) -> StreamingResponse:
    try:
        response = await asyncio.to_thread(ready_plus_download, item_id, token, x_ready_plus_key)
    except Exception as exc:
        raise _ready_plus_error(exc) from exc

    filename = f"{item_id}.zip"
    return StreamingResponse(
        response.iter_content(chunk_size=1024 * 64),
        media_type=response.headers.get("content-type") or "application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=BackgroundTask(response.close),
    )


def _run_momo_permission_check(request: MomoPermissionCheckRequest) -> MomoPermissionCheckResponse:
    config = config_from_env(Path.cwd())
    request_config = request.config or {}
    config.update(request_config)
    config["PP_TOKEN"] = request.access_token
    if request.session_token:
        config["PP_SESSION_TOKEN"] = request.session_token
    if request.proxy_seeds:
        config["proxy_seeds"] = request.proxy_seeds
        config["proxy_remove_failed"] = False
    if request.proxy_seed_chains:
        config["proxy_seed_chains"] = request.proxy_seed_chains
        config["proxy_remove_failed"] = False
    if request.capture_diagnostics:
        config["dump"] = True
    if "checkout_country" not in request_config:
        config["checkout_country"] = "VN"
    if "billing_country" not in request_config:
        config["billing_country"] = "VN"
    if "provider_country" not in request_config:
        config["provider_country"] = "VN"
    if "provider_country_label" not in request_config:
        config["provider_country_label"] = "VN"
    if "browser_locale" not in request_config:
        config["browser_locale"] = "vi-VN"
    if "elements_locale" not in request_config:
        config["elements_locale"] = "vi"
    if "browser_timezone" not in request_config:
        config["browser_timezone"] = "Asia/Ho_Chi_Minh"
    if "promo_mode" not in request_config and "PP_PROMO_MODE" not in os.environ:
        config["promo_mode"] = "off"

    ctx = ExtractionContext(config=config)
    access_token, session_token = load_token(ctx)
    proxy_seeds = ctx.load_proxy_seeds()
    result = check_momo_permission(ctx, access_token, session_token, proxy_seeds)
    return MomoPermissionCheckResponse(**result)


@app.post("/api/momo-permission-check", response_model=MomoPermissionCheckResponse)
async def momo_permission_check(request: MomoPermissionCheckRequest) -> MomoPermissionCheckResponse:
    try:
        return await asyncio.to_thread(_run_momo_permission_check, request)
    except Exception as exc:
        return MomoPermissionCheckResponse(
            available=False,
            status="failed",
            error=str(exc),
        )


@app.post("/api/proxy-check", response_model=ProxyCheckResponse)
async def proxy_check(request: ProxyCheckRequest) -> ProxyCheckResponse:
    items = check_proxies(
        proxies=request.proxies,
        protocol=request.protocol,
        concurrency=request.concurrency,
        timeout_ms=request.timeout_ms,
    )
    ok = sum(1 for item in items if item.ok)
    return ProxyCheckResponse(items=items, total=len(items), ok=ok, failed=len(items) - ok)


@app.websocket("/api/extract/jobs/{job_id}/ws")
async def job_ws(websocket: WebSocket, job_id: str) -> None:
    snapshot = await job_manager.get_job(job_id)
    if snapshot is None:
        await websocket.close(code=1008)
        return
    await ws_manager.connect(job_id, websocket)
    try:
        await websocket.send_json({"type": "snapshot", "job": snapshot.model_dump(mode="json")})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(job_id, websocket)


static_dir = Path(__file__).resolve().parent.parent / "dist"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
