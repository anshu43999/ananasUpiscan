from __future__ import annotations

import asyncio
import re
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from multiprocessing.managers import SyncManager
from typing import Any

from .models import ExtractJobCreate, ExtractJobLog, ExtractJobResult, ExtractJobSnapshot, JobStatus
from .worker import drain_queue_nowait, run_extract_worker
from .ws_manager import WebSocketManager


@dataclass
class JobState:
    job_id: str
    status: JobStatus = "pending"
    logs: list[ExtractJobLog] = field(default_factory=list)
    result: ExtractJobResult | None = None
    error: str | None = None
    diagnostic_url: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    future: Any = None
    queue: Any = None


class JobManager:
    def __init__(self, ws: WebSocketManager, max_workers: int = 2) -> None:
        self._ws = ws
        self._max_workers = max_workers
        self._executor: ProcessPoolExecutor | None = None
        self._mp_manager: SyncManager | None = None
        self._jobs: dict[str, JobState] = {}
        self._lock = asyncio.Lock()
        self._poll_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._executor is None:
            self._executor = ProcessPoolExecutor(max_workers=self._max_workers)
        if self._mp_manager is None:
            from multiprocessing import Manager

            self._mp_manager = Manager()
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def shutdown(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
        if self._mp_manager is not None:
            self._mp_manager.shutdown()

    async def create_job(self, request: ExtractJobCreate) -> ExtractJobSnapshot:
        job_id = uuid.uuid4().hex
        if self._executor is None or self._mp_manager is None:
            await self.start()
        log_queue = self._mp_manager.Queue()
        state = JobState(job_id=job_id, queue=log_queue)
        payload = request.model_dump()
        loop = asyncio.get_running_loop()
        state.future = loop.run_in_executor(self._executor, run_extract_worker, job_id, payload, log_queue)
        state.status = "running"
        state.updated_at = datetime.now(timezone.utc)
        async with self._lock:
            self._jobs[job_id] = state
        await self._append_log(job_id, "job started", "info")
        return self.snapshot_state(state)

    async def get_job(self, job_id: str) -> ExtractJobSnapshot | None:
        async with self._lock:
            state = self._jobs.get(job_id)
            return self.snapshot_state(state) if state else None

    async def cancel_job(self, job_id: str) -> ExtractJobSnapshot | None:
        async with self._lock:
            state = self._jobs.get(job_id)
            if not state:
                return None
            if state.future and not state.future.done():
                state.future.cancel()
            state.status = "cancelled"
            state.updated_at = datetime.now(timezone.utc)
            snapshot = self.snapshot_state(state)
        await self._ws.broadcast(job_id, {"type": "snapshot", "job": snapshot.model_dump(mode="json")})
        return snapshot

    def snapshot_state(self, state: JobState | None) -> ExtractJobSnapshot:
        if state is None:
            raise KeyError("job not found")
        return ExtractJobSnapshot(
            job_id=state.job_id,
            status=state.status,
            logs=list(state.logs),
            result=state.result,
            error=state.error,
            diagnostic_url=state.diagnostic_url,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )

    async def _append_log(self, job_id: str, message: str, level: str = "info") -> None:
        log = ExtractJobLog(message=message, level=level if level in {"info", "warn", "error"} else "info")
        async with self._lock:
            state = self._jobs.get(job_id)
            if not state:
                return
            state.logs.append(log)
            state.updated_at = datetime.now(timezone.utc)
        await self._ws.broadcast(job_id, {"type": "log", "log": log.model_dump(mode="json")})

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            async with self._lock:
                states = list(self._jobs.values())
            for state in states:
                await self._drain_job(state)
                if state.future and state.future.done() and state.status == "running":
                    await self._finish_job(state)

    async def _drain_job(self, state: JobState) -> None:
        if state.queue is None:
            return
        for item in drain_queue_nowait(state.queue):
            if item.get("type") == "log":
                await self._append_log(state.job_id, str(item.get("message") or ""), str(item.get("level") or "info"))

    async def _finish_job(self, state: JobState) -> None:
        try:
            output = state.future.result()
        except asyncio.CancelledError:
            output = {"status": "cancelled"}
        except BaseException as exc:
            output = {"status": "failed", "error": str(exc)}

        status = output.get("status") if isinstance(output, dict) else "failed"
        async with self._lock:
            state.status = status if status in {"completed", "failed", "cancelled"} else "failed"
            state.error = output.get("error") if isinstance(output, dict) else None
            state.result = self._result_from_logs(state.logs) if state.status == "completed" else None
            state.updated_at = datetime.now(timezone.utc)
            snapshot = self.snapshot_state(state)
        await self._ws.broadcast(state.job_id, {"type": "snapshot", "job": snapshot.model_dump(mode="json")})

    def _result_from_logs(self, logs: list[ExtractJobLog]) -> ExtractJobResult | None:
        for log in reversed(logs):
            match = re.search(r"(?:UPI|iDEAL|MoMo) final payment URL:\s*(https?://[^\s]+)", log.message)
            if match:
                return ExtractJobResult(url=match.group(1), status="ok")
        for log in reversed(logs):
            match = re.search(r"https?://payments\.stripe\.com/upi/instructions/[^\s]+", log.message)
            if match:
                return ExtractJobResult(url=match.group(0), status="ok")
        for log in reversed(logs):
            match = re.search(r"https?://[^\s]+", log.message)
            if match:
                return ExtractJobResult(url=match.group(0), status="ok")
        return None
