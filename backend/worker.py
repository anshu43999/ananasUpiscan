from __future__ import annotations

import queue
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any

from .extractor.context import ExtractionContext
from .extractor.extract import config_from_env, load_token, run_single_link_mode


class QueueExtractionContext(ExtractionContext):
    def __init__(self, config: dict[str, Any], log_queue: Queue) -> None:
        super().__init__(config=config)
        self._log_queue = log_queue

    def log(self, message: str, prefix: str = "") -> None:
        super().log(message, prefix)
        level = "error" if "[ERROR]" in prefix else "warn" if "[WARN]" in prefix else "info"
        try:
            self._log_queue.put({"type": "log", "level": level, "message": str(message)})
        except Exception:
            pass


def run_extract_worker(job_id: str, payload: dict[str, Any], log_queue: Queue) -> dict[str, Any]:
    config = config_from_env(Path.cwd())
    request_config = payload.get("config") or {}
    config.update(request_config)
    config["PP_TOKEN"] = payload["access_token"]
    if payload.get("session_token"):
        config["PP_SESSION_TOKEN"] = payload["session_token"]
    if payload.get("billing_country"):
        config["billing_country"] = payload["billing_country"]
        if "provider_country" not in request_config:
            config["provider_country"] = payload["billing_country"]
    if payload.get("proxy_seeds"):
        config["proxy_seeds"] = payload["proxy_seeds"]
        config["proxy_remove_failed"] = False
    if payload.get("capture_diagnostics"):
        config["dump"] = True

    ctx = QueueExtractionContext(config=config, log_queue=log_queue)
    try:
        access_token, session_token = load_token(ctx)
        proxy_seeds = ctx.load_proxy_seeds()
        exit_code = run_single_link_mode(ctx, access_token, session_token, proxy_seeds)
        if exit_code == 0:
            log_queue.put({"type": "log", "level": "info", "message": "worker completed"})
            return {"status": "completed", "result": None}
        return {"status": "failed", "error": "all extraction attempts failed"}
    except BaseException as exc:
        try:
            log_queue.put({"type": "log", "level": "error", "message": str(exc)})
        except Exception:
            pass
        return {"status": "failed", "error": str(exc)}
    finally:
        try:
            log_queue.put({"type": "done", "job_id": job_id})
        except Exception:
            pass


def drain_queue_nowait(log_queue: Queue) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while True:
        try:
            items.append(log_queue.get_nowait())
        except queue.Empty:
            return items
