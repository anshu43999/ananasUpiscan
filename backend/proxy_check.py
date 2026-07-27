from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

try:
    from curl_cffi.requests import Session as CurlCffiSession
except ImportError:
    CurlCffiSession = None

from .extractor.proxy import normalize_proxy_url, set_proxy
from .models import ProxyCheckItem


CHECK_URLS = (
    "https://api.ipify.org?format=json",
    "https://httpbin.org/ip",
)


def parse_proxy_lines(value: str) -> list[str]:
    return [
        line.strip()
        for line in str(value or "").replace(",", "\n").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _new_check_session(proxy: str) -> Any:
    if CurlCffiSession is not None:
        session = CurlCffiSession(impersonate="chrome136")
    else:
        session = requests.Session()
    if hasattr(session, "trust_env"):
        session.trust_env = False
    set_proxy(session, proxy)
    return session


def _extract_ip(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("ip") or payload.get("origin")
    if isinstance(value, str):
        return value.split(",")[0].strip()
    return ""


def check_one_proxy(index: int, raw: str, protocol: str, timeout_ms: int) -> ProxyCheckItem:
    started = time.perf_counter()
    proxy = normalize_proxy_url(raw, protocol)
    if not proxy:
        return ProxyCheckItem(id=index, raw=raw, proxy="", ok=False, status="格式无效", error="无法解析代理格式")

    timeout = max(1, timeout_ms / 1000)
    session = _new_check_session(proxy)
    last_error = ""

    try:
        for url in CHECK_URLS:
            try:
                response = session.get(url, timeout=timeout)
                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code >= 400:
                    last_error = f"HTTP {status_code}"
                    continue
                ip = _extract_ip(response.json())
                latency_ms = int((time.perf_counter() - started) * 1000)
                return ProxyCheckItem(
                    id=index,
                    raw=raw,
                    proxy=proxy,
                    ok=True,
                    ip=ip or None,
                    status="连通",
                    latency_ms=latency_ms,
                )
            except Exception as error:  # noqa: BLE001 - preserve concrete proxy failure message
                last_error = str(error)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    latency_ms = int((time.perf_counter() - started) * 1000)
    return ProxyCheckItem(
        id=index,
        raw=raw,
        proxy=proxy,
        ok=False,
        status="失败",
        latency_ms=latency_ms,
        error=last_error[:300] or "代理不可用",
    )


def check_proxies(proxies: str, protocol: str, concurrency: int, timeout_ms: int) -> list[ProxyCheckItem]:
    lines = parse_proxy_lines(proxies)
    if not lines:
        return []

    workers = min(max(1, concurrency), len(lines))
    results: list[ProxyCheckItem | None] = [None] * len(lines)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(check_one_proxy, index + 1, raw, protocol, timeout_ms): index
            for index, raw in enumerate(lines)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as error:  # noqa: BLE001
                raw = lines[index]
                results[index] = ProxyCheckItem(
                    id=index + 1,
                    raw=raw,
                    proxy=normalize_proxy_url(raw, protocol),
                    ok=False,
                    status="异常",
                    error=str(error)[:300],
                )

    return [item for item in results if item is not None]
