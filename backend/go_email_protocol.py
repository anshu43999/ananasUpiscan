from __future__ import annotations

import base64
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen


DEFAULT_GO_EMAIL_PROTOCOL_URL = "http://127.0.0.1:18765"


def normalize_email_protocol_backend(value: Any) -> str:
    text = str(value or "python").strip().lower().replace("-", "_")
    if text in {"go", "golang", "go_worker", "go_daemon"}:
        return "go"
    return "python"


def _base_url(config: dict[str, Any]) -> str:
    for key in ("go_email_protocol_url", "email_protocol_go_url", "go_worker_url"):
        raw = str(config.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")
    for key in ("GO_EMAIL_PROTOCOL_URL", "UPISCAN_GO_EMAIL_PROTOCOL_URL"):
        raw = str(os.environ.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")
    return DEFAULT_GO_EMAIL_PROTOCOL_URL


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None, *, headers: dict[str, str] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    body = None
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method.upper())
    parsed = urlparse(url)
    opener = build_opener(ProxyHandler({})) if parsed.hostname in {"127.0.0.1", "localhost", "::1"} else None
    try:
        if opener is not None:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        else:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"Go email protocol worker HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot connect Go email protocol worker at {url}: {exc.reason}") from exc
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Go email protocol worker returned non-JSON: {raw[:300]}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Go email protocol worker returned invalid type: {type(data).__name__}")
    return data


def check_go_email_protocol_health(config: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
    base = _base_url(config)
    data = _http_json("GET", f"{base}/health", timeout=timeout)
    status = str(data.get("status") or "").lower()
    if status and status not in {"ok", "healthy", "up"} and not data.get("ok", True):
        raise RuntimeError(f"Go email protocol worker health check failed: {data}")
    return data


def worker_supports_batches(config: dict[str, Any] | None = None, *, timeout: float = 2.0) -> bool:
    return worker_supports_feature("email-register-batches", config, timeout=timeout)


def worker_supports_feature(feature: str, config: dict[str, Any] | None = None, *, timeout: float = 2.0) -> bool:
    try:
        data = check_go_email_protocol_health(config or {}, timeout=timeout)
    except Exception:
        return False
    features = data.get("features")
    return isinstance(features, list) and feature in features


def _proxy_styles(config: dict[str, Any]) -> list[str]:
    raw = config.get("proxy_seed_styles") or config.get("proxy_styles") or "bestgo,1024"
    if isinstance(raw, (list, tuple)):
        parts = [str(item).strip() for item in raw]
    else:
        parts = [item.strip() for item in str(raw).split(",")]
    return [item for item in parts if item] or ["bestgo", "1024"]


def _batch_mailbox_provider(config: dict[str, Any]) -> str:
    raw = str(config.get("mailbox_provider") or config.get("email_resource_provider") or "outlook_token").strip().lower()
    if raw in {"", "outlook", "hotmail", "graph", "outlook_token"}:
        return "outlook_token"
    return raw


def start_go_registration_batch(*, count: int, config: dict[str, Any], batch_id: str = "", max_concurrent: int | None = None) -> dict[str, Any]:
    total = max(1, int(count or 1))
    batch_key = str(batch_id or "").strip() or f"go_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    if max_concurrent is None:
        max_concurrent = int(config.get("max_register_tasks") or config.get("go_batch_max_concurrent") or total)
    max_concurrent = max(1, min(int(max_concurrent or 1), total))
    otp_timeout = max(60, min(240, int(config.get("email_otp_timeout") or config.get("otp_timeout_seconds") or 120)))
    timeout_seconds = int(config.get("go_batch_timeout_seconds") or config.get("go_email_protocol_timeout_seconds") or (otp_timeout + 90))
    region_raw = str(
        config.get("proxy_regions")
        or config.get("proxy_seed_region")
        or config.get("proxy_region")
        or config.get("lajiao_proxy_expected_country")
        or "JP,US,DE,GB,BR"
    ).strip().upper()
    regions = [part.strip() for part in region_raw.split(",") if len(part.strip()) == 2]
    if not regions:
        regions = ["JP", "US", "DE", "GB", "BR"]
    email_tries = max(1, min(20, int(config.get("email_tries") or config.get("go_batch_email_tries") or 5)))
    payload = {
        "batch_id": batch_key,
        "count": total,
        "max_concurrent": max_concurrent,
        "mailbox_provider": _batch_mailbox_provider(config),
        "proxy_styles": _proxy_styles(config),
        "proxy_region": ",".join(regions),
        "proxy_regions": regions,
        "proxy_ttl_seconds": int(config.get("proxy_ttl_seconds") or config.get("proxy_seed_ttl") or 15),
        "otp_timeout_seconds": otp_timeout,
        "timeout_seconds": timeout_seconds,
        "email_tries": email_tries,
        "skip_phone": bool(config.get("mailat_protocol_skip_phone", True)),
    }
    return _http_json("POST", f"{_base_url(config)}/v2/email-register-batches", payload, timeout=30)


def get_go_registration_batch(batch_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    key = str(batch_id or "").strip()
    if not key:
        raise RuntimeError("Go batch id is required.")
    return _http_json("GET", f"{_base_url(config or {})}/v2/email-register-batches/{key}", timeout=15)


def cancel_go_registration_batch(batch_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    key = str(batch_id or "").strip()
    if not key:
        raise RuntimeError("Go batch id is required.")
    return _http_json("DELETE", f"{_base_url(config or {})}/v2/email-register-batches/{key}", timeout=15)


def verify_go_plus_batch(
    items: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    *,
    workers: int = 32,
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    payload = {
        "items": items,
        "workers": max(1, min(100, int(workers or 32))),
        "timeout_ms": max(3000, min(60000, int(timeout_ms or 15000))),
    }
    return _http_json("POST", f"{_base_url(config or {})}/v2/plus-verify", payload, timeout=max(30.0, len(items) * 2.0))


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        segment = token.split(".")[1]
        segment += "=" * ((4 - len(segment) % 4) % 4)
        payload = base64.urlsafe_b64decode(segment.encode("ascii")).decode("utf-8")
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _access_token_from_payload(payload: dict[str, Any]) -> str:
    for key in ("access_token", "accessToken", "chatgpt_access_token_initial"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    session = payload.get("session")
    if isinstance(session, dict):
        for key in ("access_token", "accessToken"):
            value = str(session.get(key) or "").strip()
            if value:
                return value
    return ""


def _normalize_socks_url(proxy_url: str) -> str:
    raw = str(proxy_url or "").strip()
    if not raw:
        return ""
    line = next((part.strip() for part in raw.replace("\r", "\n").split("\n") if part.strip() and not part.strip().startswith("#")), raw)
    lower = line.lower()
    if lower.startswith("socks5h://"):
        return "socks5://" + line.split("://", 1)[1]
    if lower.startswith("socks5://"):
        return line
    if lower.startswith(("http://", "https://")):
        rest = line.split("://", 1)[1]
        if rest.startswith(("127.0.0.1", "localhost", "[::1]")):
            return "http://" + rest
        return "socks5://" + rest
    if "://" not in line:
        return f"socks5://{line}"
    return line


def _resource_grant(config: dict[str, Any], *, email: str, proxy_url: str) -> dict[str, Any]:
    bridge_url = _normalize_socks_url(proxy_url)
    expected_country = str(config.get("proxy_seed_region") or config.get("proxy_region") or config.get("lajiao_proxy_expected_country") or "").split(",", 1)[0].strip().upper()
    return {
        "email_key": str(config.get("outlook_email") or email),
        "proxy_key": proxy_url or "direct",
        "lease_fence": int(time.time()),
        "exit_ip": str(config.get("registration_proxy_exit_ip") or ""),
        "expected_country": expected_country,
        "bridge": {
            "id": "direct-socks" if bridge_url and not bridge_url.startswith("http://127.0.0.1") else "direct",
            "url": bridge_url,
            "capability": "direct",
            "generation": 1,
            "protocol": "socks5" if bridge_url.startswith("socks5://") else "http-connect",
        },
    }


def _cap_headers(capability: str) -> dict[str, str]:
    cap = str(capability or "").strip()
    return {"X-Job-Capability": cap, "Authorization": f"Bearer {cap}"} if cap else {}


def _result_from_payload(payload: dict[str, Any], *, email: str, password: str, task_id: str, config: dict[str, Any]) -> dict[str, Any]:
    token = _access_token_from_payload(payload)
    if not token:
        raise RuntimeError(f"Go email protocol finished but did not return access_token: {payload.get('message') or payload.get('failure_code') or payload.get('error') or payload}")
    claims = _decode_jwt_payload(token)
    auth = claims.get("https://api.openai.com/auth") if isinstance(claims.get("https://api.openai.com/auth"), dict) else {}
    profile = claims.get("https://api.openai.com/profile") if isinstance(claims.get("https://api.openai.com/profile"), dict) else {}
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    resolved_email = str(session.get("email") or payload.get("email") or profile.get("email") or claims.get("email") or email)
    account_id = str(session.get("account_id") or payload.get("account_id") or auth.get("chatgpt_account_id") or claims.get("sub") or "")
    plan_type = str(session.get("plan_type") or payload.get("plan_type") or auth.get("chatgpt_plan_type") or "free")
    storage_path = ""
    if session:
        path = Path.cwd() / "data" / "registered_accounts" / f"go_storage_{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        storage_path = str(path)
    return {
        "success": True,
        "status": "email_registered",
        "stage": "manual_plus_required",
        "registration_mode": "email",
        "registration_status": "registered",
        "task_id": task_id,
        "email": resolved_email,
        "account_id": account_id,
        "password": password,
        "generated_chatgpt_password": password,
        "plan_type": plan_type,
        "access_token": token,
        "chatgpt_access_token_initial": token,
        "browser_engine": "go_email_protocol",
        "browser_profile_dir": "",
        "browser_storage_state_path": storage_path,
        "email_register_flow": str(config.get("email_register_flow") or "go"),
        "protocol_backend": "go",
        "go_job_id": str(payload.get("job_id") or ""),
        "state": payload,
    }


def run_go_email_protocol(
    config: dict[str, Any],
    *,
    email: str,
    password: str,
    otp_callback: Callable[[], str],
    task_id: str,
    log: Callable[[str], None],
) -> dict[str, Any]:
    if not email:
        raise RuntimeError("Go email protocol registration missing email.")
    if not password:
        raise RuntimeError("Go email protocol registration missing password.")
    base = _base_url(config)
    timeout_seconds = max(120, int(config.get("go_email_protocol_timeout_seconds") or config.get("email_register_timeout") or 900))
    poll_s = max(0.5, min(10.0, float(config.get("go_email_protocol_poll_interval_ms") or 1000) / 1000.0))
    proxy_url = str(config.get("proxy") or config.get("mailat_protocol_proxy") or "").strip()
    health = check_go_email_protocol_health(config)
    log(f"[go-protocol] health ok: {health.get('service') or health.get('runner') or health.get('status') or 'worker'}")

    request_fingerprint = f"{task_id}:{email}:{proxy_url}:{secrets.token_hex(4)}"
    body: dict[str, Any] = {
        "task_id": f"{task_id}_{secrets.token_hex(3)}",
        "attempt_id": 1,
        "idempotency_key": f"idem_{task_id}_{secrets.token_hex(4)}",
        "request_fingerprint": f"sha256:{secrets.token_hex(32)}",
        "email": email,
        "password": password,
        "resource_grant": _resource_grant(config, email=email, proxy_url=proxy_url),
        "profile": {"id": f"profile_{request_fingerprint[:24]}"},
        "skip_phone": bool(config.get("mailat_protocol_skip_phone", True)),
        "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    client_id = str(config.get("outlook_client_id") or config.get("oauth_client_id") or "").strip()
    refresh_token = str(config.get("outlook_refresh_token") or "").strip()
    if client_id and refresh_token:
        body["mailbox_client_id"] = client_id
        body["mailbox_refresh_token"] = refresh_token
        body["otp_timeout_seconds"] = int(config.get("email_otp_timeout") or 200)
        log(f"[go-protocol] in-worker Outlook Graph OTP enabled: client_id={client_id[:8]}...")

    log(f"[go-protocol] POST {base}/v2/email-register email={email}")
    payload = _http_json("POST", f"{base}/v2/email-register", body, timeout=min(60.0, float(timeout_seconds)))
    job_id = str(payload.get("job_id") or "").strip()
    capability = str(payload.get("job_capability") or "").strip()
    if not job_id:
        return _result_from_payload(payload, email=email, password=password, task_id=task_id, config=config)

    headers = _cap_headers(capability)
    deadline = time.monotonic() + timeout_seconds
    otp_submitted_for = ""
    while time.monotonic() < deadline:
        status = str(payload.get("status") or "").strip().lower()
        if status in {"succeeded", "completed", "success", "ok"} or _access_token_from_payload(payload):
            return _result_from_payload(payload, email=email, password=password, task_id=task_id, config=config)
        if status in {"failed", "error", "cancelled", "reconcile_required"}:
            message = str(payload.get("message") or payload.get("failure_code") or payload.get("error") or status)
            raise RuntimeError(f"Go email protocol registration failed: {message}")
        if status in {"waiting_for_otp", "need_otp", "awaiting_otp", "otp_required"}:
            challenge = payload.get("challenge") if isinstance(payload.get("challenge"), dict) else {}
            challenge_id = str(challenge.get("challenge_id") or payload.get("challenge_id") or "").strip()
            if body.get("mailbox_client_id"):
                log(f"[go-protocol] worker is waiting for in-worker OTP: challenge={challenge_id or '-'}")
            elif challenge_id != otp_submitted_for:
                code = str(otp_callback() or "").strip()
                if not code:
                    raise RuntimeError("Go email protocol requested OTP but OTP callback returned empty.")
                otp_body = {
                    "challenge_id": challenge_id,
                    "state_version": int(challenge.get("state_version") or payload.get("state_version") or 0),
                    "code": code,
                }
                payload = _http_json("POST", f"{base}/v2/email-register/{job_id}/otp", otp_body, headers=headers, timeout=30)
                otp_submitted_for = challenge_id
                capability = str(payload.get("job_capability") or capability)
                headers = _cap_headers(capability)
                continue
        time.sleep(poll_s)
        payload = _http_json("GET", f"{base}/v2/email-register/{job_id}?wait_ms={int(poll_s * 1000)}", headers=headers, timeout=30)
    raise TimeoutError(f"Go email protocol registration exceeded {timeout_seconds}s")
