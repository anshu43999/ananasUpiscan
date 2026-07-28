from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .job_manager import JobManager
from .models import (
    ExtractJobCreate,
    ExtractJobCreated,
    ExtractJobSnapshot,
    MomoPermissionCheckRequest,
    MomoPermissionCheckResponse,
    ProxyCheckRequest,
    ProxyCheckResponse,
    ProxyChainTestResult,
)
from .extractor.context import ExtractionContext
from .extractor.extract import config_from_env, load_token
from .extractor.vietnam import check_momo_permission
from .proxy_check import check_proxies
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
