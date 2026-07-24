from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .job_manager import JobManager
from .models import (
    ExtractConfig,
    ExtractJobCreate,
    ExtractJobCreated,
    ExtractJobSnapshot,
    ExtractSettings,
    ProxyChainTestResult,
)
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


@app.get("/api/config", response_model=ExtractConfig)
async def get_config() -> ExtractConfig:
    return ExtractConfig()


@app.get("/api/settings", response_model=ExtractSettings)
async def get_settings() -> ExtractSettings:
    return ExtractSettings()


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
