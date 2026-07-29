from __future__ import annotations

import os
from typing import Any
from urllib.parse import urljoin

import requests


class ReadyPlusApiError(RuntimeError):
    def __init__(self, status_code: int, payload: Any, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _api_base() -> str:
    return os.environ.get("READY_PLUS_API_BASE", "https://api.cli-proxy.cn").strip().rstrip("/")


def _api_key(api_key: str | None = None) -> str:
    value = (api_key or os.environ.get("READY_PLUS_API_KEY", "")).strip()
    if not value:
        raise RuntimeError("Ready Plus API key is required")
    return value


def _headers(idempotency_key: str | None = None, api_key: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {_api_key(api_key)}",
        "Accept": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _url(path: str) -> str:
    return urljoin(f"{_api_base()}/", path.lstrip("/"))


def _error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or fallback)
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return fallback


def ready_plus_json(
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    api_key: str | None = None,
) -> Any:
    try:
        response = requests.request(
            method,
            _url(path),
            headers=_headers(idempotency_key, api_key),
            json=json_body,
            params=params,
            timeout=float(os.environ.get("READY_PLUS_TIMEOUT", "30")),
        )
    except requests.RequestException as exc:
        raise ReadyPlusApiError(502, {"ok": False, "error": {"code": "ready_plus_network_error", "message": str(exc)}}, str(exc)) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {"ok": False, "error": {"code": "ready_plus_invalid_response", "message": response.text[:500]}}

    if response.status_code >= 400:
        raise ReadyPlusApiError(response.status_code, payload, _error_message(payload, f"Ready Plus request failed ({response.status_code})"))
    return payload


def ready_plus_download(item_id: str, token: str, api_key: str | None = None) -> requests.Response:
    try:
        response = requests.get(
            _url(f"/api/v1/items/{item_id}/download"),
            headers=_headers(api_key=api_key),
            params={"token": token},
            stream=True,
            timeout=float(os.environ.get("READY_PLUS_DOWNLOAD_TIMEOUT", "120")),
        )
    except requests.RequestException as exc:
        raise ReadyPlusApiError(502, {"ok": False, "error": {"code": "ready_plus_network_error", "message": str(exc)}}, str(exc)) from exc

    if response.status_code >= 400:
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"ok": False, "error": {"code": "ready_plus_download_failed", "message": response.text[:500]}}
        response.close()
        raise ReadyPlusApiError(response.status_code, payload, _error_message(payload, f"Ready Plus download failed ({response.status_code})"))
    return response
