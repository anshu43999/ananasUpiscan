from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

try:
    from curl_cffi import requests as curl_requests
except Exception:  # pragma: no cover - optional runtime dependency
    curl_requests = None

import requests

from .extractor.proxy import normalize_proxy_url, proxy_label


LogFn = Callable[[str, str], None]

IPPURE_INFO_URL = "https://my.ippure.com/v1/info"


@dataclass
class ProxyPrecheckResult:
    ok: bool
    proxy: str
    exit_ip: str = ""
    country: str = ""
    fraud_score: int | None = None
    is_residential: bool | None = None
    latency_ms: int = 0
    reason: str = ""


def _cfg_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    value = payload.get(key, config.get(key, os.environ.get(f"UPISCAN_{key.upper()}")))
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _cfg_int(payload: dict[str, Any], key: str, default: int, minimum: int = 1, maximum: int = 120) -> int:
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    value = payload.get(key, config.get(key, os.environ.get(f"UPISCAN_{key.upper()}", default)))
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _request_get(url: str, proxy: str, timeout: int, headers: dict[str, str] | None = None):
    if curl_requests is not None:
        return curl_requests.get(url, proxy=proxy, timeout=timeout, impersonate="chrome136", allow_redirects=True, headers=headers)
    return requests.get(url, proxies={"http": proxy, "https": proxy}, timeout=timeout, allow_redirects=True, headers=headers)


def _response_json(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"IPPure 未返回 JSON: {str(getattr(response, 'text', '') or '')[:160]}") from exc
    return data if isinstance(data, dict) else {"data": data}


def _fraud_score(data: dict[str, Any]) -> int | None:
    value = data.get("fraudScore")
    try:
        return int(value)
    except Exception:
        return None


def precheck_proxy(proxy: str, payload: dict[str, Any], used_ips: set[str] | None = None) -> ProxyPrecheckResult:
    started = time.perf_counter()
    timeout = _cfg_int(payload, "proxy_precheck_timeout", 12, minimum=2, maximum=60)
    max_fraud_score = _cfg_int(payload, "proxy_precheck_max_fraud_score", 50, minimum=0, maximum=100)
    normalized = normalize_proxy_url(proxy, "http")
    if not normalized:
        return ProxyPrecheckResult(False, proxy, reason="format_invalid")

    try:
        response = _request_get(IPPURE_INFO_URL, normalized, timeout)
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            return ProxyPrecheckResult(False, normalized, latency_ms=int((time.perf_counter() - started) * 1000), reason=f"ippure_http_{status}")
        data = _response_json(response)
    except Exception as exc:
        return ProxyPrecheckResult(False, normalized, latency_ms=int((time.perf_counter() - started) * 1000), reason=f"ippure_failed: {str(exc)[:160]}")

    score = _fraud_score(data)
    exit_ip = str(data.get("ip") or "").strip()
    country = str(data.get("countryCode") or "").strip().upper()
    residential_raw = data.get("isResidential")
    is_residential = residential_raw if isinstance(residential_raw, bool) else None
    if score is None:
        return ProxyPrecheckResult(False, normalized, exit_ip=exit_ip, country=country, is_residential=is_residential, latency_ms=int((time.perf_counter() - started) * 1000), reason="ippure_missing_fraud_score")
    if score > max_fraud_score:
        return ProxyPrecheckResult(False, normalized, exit_ip=exit_ip, country=country, fraud_score=score, is_residential=is_residential, latency_ms=int((time.perf_counter() - started) * 1000), reason=f"fraud_score_high:{score}>{max_fraud_score}")
    return ProxyPrecheckResult(True, normalized, exit_ip=exit_ip, country=country, fraud_score=score, is_residential=is_residential, latency_ms=int((time.perf_counter() - started) * 1000))


def filter_clean_proxies(
    proxies: list[str],
    payload: dict[str, Any],
    *,
    log: LogFn | None = None,
    target_count: int = 1,
) -> list[str]:
    if not proxies or not _cfg_bool(payload, "proxy_precheck_enabled", True):
        return proxies
    max_checks = _cfg_int(payload, "proxy_precheck_max_candidates", max(len(proxies), target_count), minimum=1, maximum=500)
    min_clean = max(1, min(len(proxies), int(target_count or 1)))
    clean: list[str] = []
    for index, proxy in enumerate(proxies[:max_checks], start=1):
        result = precheck_proxy(proxy, payload)
        label = proxy_label(proxy)
        if result.ok:
            clean.append(result.proxy)
            if log:
                log(f"IPPure 风险检测通过 {index}/{len(proxies)}: {label} ip={result.exit_ip or '?'} score={result.fraud_score if result.fraud_score is not None else '?'} residential={result.is_residential} country={result.country or '?'} latency={result.latency_ms}ms", "info")
            if len(clean) >= min_clean:
                break
        elif log:
            log(f"IPPure 风险检测跳过 {index}/{len(proxies)}: {label} reason={result.reason} ip={result.exit_ip or '?'} score={result.fraud_score if result.fraud_score is not None else '?'}", "warn")
    if log:
        log(f"IPPure 风险检测完成：可用 {len(clean)}/{min(len(proxies), max_checks)}，需要 {min_clean}", "info" if clean else "error")
    return clean
