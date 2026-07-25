from __future__ import annotations

import os
import queue
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any

from .extractor.context import ExtractionContext
from .extractor.extract import config_from_env, load_token, run_single_link_mode
from .extractor.ideal import run_ideal_single_link_mode


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
    payment_method = str(payload.get("payment_method") or "upi").strip().lower()
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
    if payload.get("proxy_seed_chains"):
        config["proxy_seed_chains"] = payload["proxy_seed_chains"]
        config["proxy_remove_failed"] = False
    if payload.get("capture_diagnostics"):
        config["dump"] = True

    if payment_method == "ideal":
        if "bootstrap_country" not in request_config:
            config["bootstrap_country"] = "JP"
        if "promotion_countries" not in request_config:
            config["promotion_countries"] = ["NL"]
        if "provider_country" not in request_config:
            config["provider_country"] = "NL"
        if "provider_country_label" not in request_config:
            config["provider_country_label"] = "NL"
        if "billing_country" not in request_config or str(config.get("billing_country") or "").upper() == "IN":
            config["billing_country"] = "NL"
        if "checkout_country" not in request_config:
            config["checkout_country"] = os.environ.get("IDEAL_CHECKOUT_COUNTRY", "NL").strip() or "NL"
        if "browser_locale" not in request_config:
            config["browser_locale"] = os.environ.get("IDEAL_BROWSER_LOCALE", "nl-NL").strip() or "nl-NL"
        if "elements_locale" not in request_config:
            config["elements_locale"] = os.environ.get("IDEAL_ELEMENTS_LOCALE", "nl").strip() or "nl"
        if "browser_timezone" not in request_config:
            config["browser_timezone"] = os.environ.get("IDEAL_BROWSER_TIMEZONE", "Europe/Amsterdam").strip() or "Europe/Amsterdam"
        if "ideal_max_minor_amount" not in request_config and "IDEAL_MAX_MINOR_AMOUNT" not in os.environ:
            config["ideal_max_minor_amount"] = 50

    ctx = QueueExtractionContext(config=config, log_queue=log_queue)
    try:
        access_token, session_token = load_token(ctx)
        proxy_seeds = ctx.load_proxy_seeds()
        if payment_method == "ideal":
            exit_code = run_ideal_single_link_mode(ctx, access_token, session_token, proxy_seeds)
        elif payment_method == "upi":
            exit_code = run_single_link_mode(ctx, access_token, session_token, proxy_seeds)
        else:
            raise RuntimeError(f"unsupported payment_method: {payment_method}")
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
