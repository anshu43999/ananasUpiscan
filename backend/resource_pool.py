from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DB_LOCK = threading.Lock()
INIT_DONE: set[str] = set()

VALID_STATUSES = {"available", "leased", "used", "cooldown", "disabled"}
LEASE_TTL_SECONDS = 30 * 60
BIND_PHONE_FAILURE_COOLDOWN_SECONDS = 60 * 60

BIND_PHONE_OUTCOME_SUCCESS = "success"
BIND_PHONE_OUTCOME_RELEASED = "released"
BIND_PHONE_OUTCOME_RECENTLY_USED = "recently_used"
BIND_PHONE_OUTCOME_INVALID = "invalid"
BIND_PHONE_OUTCOME_TIMEOUT = "timeout"
BIND_PHONE_OUTCOME_OTP_SUBMIT_FAILED = "otp_submit_failed"
BIND_PHONE_OUTCOME_TRANSPORT_FAILED = "transport_failed"

BIND_PHONE_FAILURE_RELEASED = "bind_phone_released"
BIND_PHONE_FAILURE_RECENTLY_USED = "phone_recently_used"
BIND_PHONE_FAILURE_INVALID_NUMBER = "phone_invalid_number"
BIND_PHONE_FAILURE_INVALID_OTP = "phone_invalid_otp"
BIND_PHONE_FAILURE_TIMEOUT = "phone_timeout"
BIND_PHONE_FAILURE_OTP_SUBMIT_FAILED = "phone_otp_submit_failed"
BIND_PHONE_FAILURE_OTP_SUBMIT_STATUS_0 = "phone_otp_submit_status_0"
BIND_PHONE_FAILURE_OTP_SUBMIT_TRANSPORT = "phone_otp_submit_transport_failed"
BIND_PHONE_FAILURE_TRANSPORT = "phone_transport_failed"

SMS_TIMEOUT_MARKERS = (
    "sms timeout",
    "otp timeout",
    "awaiting_sms_code",
    "no sms",
    "未收到短信",
    "短信超时",
    "验证码超时",
    "接码超时",
)
PHONE_USED_MARKERS = (
    "old phone",
    "existing account",
    "phone number is already",
    "phone already",
    "already registered with this phone",
    "already used",
    "recently used",
    "try again later",
    "already associated",
    "associated with the maximum",
    "maximum number of accounts",
    "limit reached",
    "too many accounts",
    "手机号已注册",
    "号码已注册",
    "旧号",
    "老号",
)
PHONE_INVALID_MARKERS = (
    "invalid phone",
    "invalid number",
    "phone number is invalid",
    "手机号无效",
    "号码无效",
)
NETWORK_MARKERS = (
    "network error",
    "connection timeout",
    "read timed out",
    "connection reset",
    "err_empty_response",
    "err_connection_closed",
    "err_connection_reset",
    "err_connection_timed_out",
    "status=403",
    "status 403",
    "cloudflare",
    "captcha",
    "turnstile",
)
BIND_PHONE_STATUS_ZERO_MARKERS = (
    "status 0",
    "status=0",
    "status: 0",
    BIND_PHONE_FAILURE_OTP_SUBMIT_STATUS_0,
)
BIND_PHONE_OTP_INVALID_MARKERS = (
    "invalid otp",
    "invalid code",
    "incorrect",
    "wrong",
    "expired",
    "验证码错误",
    BIND_PHONE_FAILURE_INVALID_OTP,
)
BIND_PHONE_OTP_SUBMIT_FAILURE_MARKERS = (
    "otp submit",
    "phone-otp",
    "验证码提交失败",
    BIND_PHONE_FAILURE_OTP_SUBMIT_FAILED,
    BIND_PHONE_FAILURE_OTP_SUBMIT_STATUS_0,
    BIND_PHONE_FAILURE_OTP_SUBMIT_TRANSPORT,
)


@dataclass
class ResourceLease:
    id: int = 0
    resource_type: str = ""
    provider: str = ""
    resource_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    lease_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ResourceLease":
        data = data or {}
        payload = data.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        return cls(
            id=int(data.get("id") or 0),
            resource_type=str(data.get("resource_type") or ""),
            provider=str(data.get("provider") or ""),
            resource_key=str(data.get("resource_key") or ""),
            payload=payload,
            status=str(data.get("status") or ""),
            lease_id=str(data.get("lease_id") or ""),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def cooldown_until(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds or 0)))).isoformat()


def resource_db_path() -> Path:
    configured = os.environ.get("UPISCAN_RESOURCE_DB", "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / "data" / "resource_pool.sqlite3"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None) -> Any:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else resource_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    init_db(conn, str(path.resolve()))
    return conn


def init_db(conn: sqlite3.Connection, key: str) -> None:
    if key in INIT_DONE:
        return
    with DB_LOCK:
        if key in INIT_DONE:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS resource_pool (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              resource_type TEXT NOT NULL,
              provider TEXT NOT NULL,
              resource_key TEXT NOT NULL,
              payload_json TEXT DEFAULT '{}',
              status TEXT DEFAULT 'available',
              lease_id TEXT DEFAULT '',
              success_count INTEGER DEFAULT 0,
              fail_count INTEGER DEFAULT 0,
              cooldown_until TEXT DEFAULT '',
              last_error TEXT DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(resource_type, provider, resource_key)
            );
            CREATE INDEX IF NOT EXISTS idx_resource_pool_lookup ON resource_pool(resource_type, provider, status);
            CREATE INDEX IF NOT EXISTS idx_resource_pool_lease ON resource_pool(lease_id);
            """
        )
        conn.commit()
        INIT_DONE.add(key)


def row_to_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    payload = loads(str(item.pop("payload_json", "") or "{}"))
    item["payload"] = payload if isinstance(payload, dict) else {}
    return item


def parse_phone_url_entries(text: str | list[str] | tuple[str, ...]) -> list[tuple[str, str]]:
    rows = [str(item or "") for item in text] if isinstance(text, (list, tuple)) else str(text or "").replace("\r", "\n").split("\n")
    entries: list[tuple[str, str]] = []
    for row in rows:
        line = row.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        normalized = line.replace("----", "|")
        parts = [part.strip() for part in normalized.split("|") if part.strip()]
        if len(parts) < 2:
            raise ValueError(f"手机号接码行格式错误，期望 phone|sms_url 或 phone----sms_url: {line[:80]}")
        phone = parts[0]
        sms_url = next((part for part in parts[1:] if part.startswith(("http://", "https://"))), parts[1])
        if not phone or not sms_url:
            raise ValueError(f"手机号接码行缺少 phone 或 sms_url: {line[:80]}")
        entries.append((phone, sms_url))
    return entries


def _ensure_registration_runtime_path() -> None:
    runtime = Path(__file__).resolve().parent / "registration_runtime"
    text = str(runtime)
    if text not in sys.path:
        sys.path.insert(0, text)


def _proxy_seed_tools():
    _ensure_registration_runtime_path()
    from core.proxy.seed_session import build_session, parse_seed, seed_from_payload  # type: ignore

    return parse_seed, seed_from_payload, build_session


def parse_proxy_seed_entries(text: str | list[str] | tuple[str, ...], *, protocol: str = "socks5", style: str = "") -> list[tuple[str, dict[str, Any]]]:
    parse_seed, _seed_from_payload, _build_session = _proxy_seed_tools()
    rows = [str(item or "") for item in text] if isinstance(text, (list, tuple)) else str(text or "").replace("\r", "\n").split("\n")
    seeds: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        line = row.strip().strip('"').strip("'")
        if not line or line.startswith("#"):
            continue
        seed = parse_seed(line, protocol=protocol, style=style)
        key = str(seed.resource_key)
        if key in seen:
            continue
        seen.add(key)
        seeds.append((key, seed.to_payload()))
    if not seeds:
        raise ValueError("请至少导入一条代理 seed，格式 account:pass@host:port")
    return seeds


def _classify_mailbox_api_link(value: str, label: str = "") -> str:
    lowered = str(value or "").strip().lower()
    label = str(label or "").strip().lower()
    if not lowered.startswith(("http://", "https://")):
        return ""
    if label == "code" or "/api/code/" in lowered:
        return "code_url"
    if (
        label == "mail"
        or "/api/mails" in lowered
        or "/api/imap/mails" in lowered
        or ("recipient=" in lowered and "/mail" in lowered)
        or "/api/mail/" in lowered
    ):
        return "mail_url"
    return "inbox_url"


def parse_link_api_mailbox_entries(text: str | list[str] | tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
    rows = [str(item or "") for item in text] if isinstance(text, (list, tuple)) else str(text or "").replace("\r", "\n").split("\n")
    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        line = row.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("----")]
        if len(parts) < 2 or "@" not in parts[0]:
            raise ValueError(f"邮箱接码行格式错误，应为 email----收信URL 或 email----code:...----mail:...: {line[:80]}")
        email = parts[0].strip()
        key = email.lower()
        payload = {"email": email, "inbox_url": "", "code_url": "", "mail_url": ""}
        for part in parts[1:]:
            if not part:
                continue
            label = ""
            value = part
            if ":" in part:
                prefix, suffix = part.split(":", 1)
                if prefix.strip().lower() in {"show", "inbox", "mail", "code"}:
                    label = prefix.strip().lower()
                    value = suffix.strip()
            kind = _classify_mailbox_api_link(value, label)
            if kind == "code_url":
                payload["code_url"] = value
            elif kind == "mail_url":
                payload["mail_url"] = value
            elif kind == "inbox_url" and not payload["inbox_url"]:
                payload["inbox_url"] = value
        if not (payload["inbox_url"] or payload["code_url"] or payload["mail_url"]):
            raise ValueError(f"邮箱接码行缺少可用收信 URL/API: {line[:80]}")
        if key in seen:
            continue
        seen.add(key)
        entries.append((key, payload))
    if not entries:
        raise ValueError("请至少导入一条邮箱接码资源")
    return entries


def parse_outlook_token_entries(text: str | list[str] | tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
    rows = [str(item or "") for item in text] if isinstance(text, (list, tuple)) else str(text or "").replace("\r", "\n").split("\n")
    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        line = row.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("----")]
        if len(parts) != 4 or "@" not in parts[0] or not parts[2] or not parts[3]:
            raise ValueError(f"Outlook token 行格式错误，应为 email----password----client_id----refresh_token: {line[:80]}")
        email, password, client_id, refresh_token = parts
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        entries.append((key, {"email": email, "password": password, "client_id": client_id, "refresh_token": refresh_token}))
    if not entries:
        raise ValueError("请至少导入一条 Outlook token 资源")
    return entries


def parse_icloud_privacy_entries(text: str | list[str] | tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
    rows = [str(item or "") for item in text] if isinstance(text, (list, tuple)) else str(text or "").replace("\r", "\n").split("\n")
    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        line = row.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("----")]
        email = parts[0].strip().lower()
        if "@" not in email:
            raise ValueError(f"iCloud 隐私邮箱格式错误: {line[:80]}")
        if email in seen:
            continue
        seen.add(email)
        payload = {"email": email}
        if len(parts) > 1:
            payload["imap_user"] = parts[1]
        if len(parts) > 2:
            payload["imap_pass"] = parts[2]
        if len(parts) > 3:
            payload["imap_host"] = parts[3]
        if len(parts) > 4:
            payload["imap_port"] = parts[4]
        entries.append((email, payload))
    if not entries:
        raise ValueError("请至少导入一条 iCloud 隐私邮箱资源")
    return entries


def parse_forwarded_domain_entries(text: str | list[str] | tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
    rows = [str(item or "") for item in text] if isinstance(text, (list, tuple)) else str(text or "").replace("\r", "\n").split("\n")
    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        line = row.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("----")]
        if len(parts) < 3:
            raise ValueError(f"Forwarded domain row must be domain----imap_user----imap_pass[----imap_host----imap_port]: {line[:80]}")
        domain, imap_user, imap_pass = parts[:3]
        domain = domain.strip().lstrip("@").lower()
        if not domain or "." not in domain or "@" not in imap_user or not imap_pass:
            raise ValueError(f"Forwarded domain row has invalid domain or IMAP credentials: {line[:80]}")
        if domain in seen:
            continue
        seen.add(domain)
        payload = {"domain": domain, "imap_user": imap_user, "imap_pass": imap_pass}
        if len(parts) > 3:
            payload["imap_host"] = parts[3]
        if len(parts) > 4:
            payload["imap_port"] = parts[4]
        entries.append((domain, payload))
    if not entries:
        raise ValueError("Please import at least one forwarded domain mailbox resource.")
    return entries


def parse_cfworker_entries(text: str | list[str] | tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
    rows = [str(item or "") for item in text] if isinstance(text, (list, tuple)) else str(text or "").replace("\r", "\n").split("\n")
    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        line = row.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("----")]
        if len(parts) < 3:
            raise ValueError(f"CFWorker row must be api_url----admin_token----domain[----fingerprint]: {line[:80]}")
        api_url, admin_token, domain = parts[:3]
        domain = domain.strip().lstrip("@").lower()
        if not api_url.startswith(("http://", "https://")) or not admin_token or not domain:
            raise ValueError(f"CFWorker row has invalid api_url/admin_token/domain: {line[:80]}")
        key = f"{api_url.rstrip('/')}|{domain}"
        if key in seen:
            continue
        seen.add(key)
        payload = {"api_url": api_url.rstrip("/"), "admin_token": admin_token, "domain": domain}
        if len(parts) > 3:
            payload["fingerprint"] = parts[3]
        entries.append((key, payload))
    if not entries:
        raise ValueError("Please import at least one CFWorker mailbox resource.")
    return entries


def _normalize_status(value: str) -> str:
    status = str(value or "available").strip().lower()
    if status not in VALID_STATUSES:
        raise ValueError(f"资源状态无效: {value}")
    return status


def import_many(resource_type: str, provider: str, rows: list[tuple[str, dict[str, Any]]], db_path: str | Path | None = None) -> int:
    pending: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for resource_key, payload in rows:
        key = str(resource_key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        pending.append((key, payload if isinstance(payload, dict) else {}))
    if not pending:
        return 0
    now = utc_now()
    inserted = 0
    with connect(db_path) as conn:
        for key, payload in pending:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO resource_pool(
                  resource_type, provider, resource_key, payload_json, status,
                  created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (resource_type, provider, key, dumps(payload), "available", now, now),
            )
            inserted += int(cur.rowcount or 0)
        conn.commit()
    return inserted


def import_phone_urls(text: str, provider: str = "user_phone_url", db_path: str | Path | None = None) -> dict[str, Any]:
    provider = str(provider or "user_phone_url").strip() or "user_phone_url"
    entries = parse_phone_url_entries(text)
    rows = [(phone, {"phone": phone, "sms_url": sms_url}) for phone, sms_url in entries]
    imported = import_many("phone", provider, rows, db_path)
    return {"ok": True, "imported": imported, "total_rows": len(rows)}


def import_proxy_seeds(
    text: str,
    *,
    provider: str = "proxy_seed",
    protocol: str = "socks5",
    style: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    provider = str(provider or "proxy_seed").strip() or "proxy_seed"
    rows = parse_proxy_seed_entries(text, protocol=protocol, style=style)
    imported = import_many("proxy", provider, rows, db_path)
    return {"ok": True, "imported": imported, "total_rows": len(rows)}


def import_email_resources(
    text: str,
    *,
    provider: str = "icloud_api",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    provider = str(provider or "icloud_api").strip() or "icloud_api"
    if provider == "outlook_token":
        rows = parse_outlook_token_entries(text)
    elif provider == "icloud_privacy":
        rows = parse_icloud_privacy_entries(text)
    elif provider == "forwarded_domain":
        rows = parse_forwarded_domain_entries(text)
    elif provider in {"cfworker_admin_api", "cfworker", "cloud_mail"}:
        provider = "cfworker_admin_api"
        rows = parse_cfworker_entries(text)
    else:
        provider = "icloud_api"
        rows = parse_link_api_mailbox_entries(text)
    imported = import_many("email", provider, rows, db_path)
    return {"ok": True, "imported": imported, "total_rows": len(rows)}


def _email_lease_to_mailbox_row(item: dict[str, Any]) -> dict[str, str]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    email = str(payload.get("email") or item.get("resource_key") or "").strip()
    provider = str(item.get("provider") or "").strip() or "icloud_api"
    row = {
        "email": email,
        "inbox_url": str(payload.get("inbox_url") or ""),
        "code_url": str(payload.get("code_url") or ""),
        "mail_url": str(payload.get("mail_url") or ""),
        "_resource_key": str(item.get("resource_key") or email),
        "_resource_provider": provider,
        "_resource_lease_id": str(item.get("lease_id") or ""),
    }
    if provider == "outlook_token":
        row.update(
            {
                "password": str(payload.get("password") or ""),
                "client_id": str(payload.get("client_id") or ""),
                "refresh_token": str(payload.get("refresh_token") or ""),
            }
        )
    elif provider == "icloud_privacy":
        row.update(
            {
                "imap_user": str(payload.get("imap_user") or ""),
                "imap_pass": str(payload.get("imap_pass") or ""),
                "imap_host": str(payload.get("imap_host") or ""),
                "imap_port": str(payload.get("imap_port") or ""),
            }
        )
    elif provider == "forwarded_domain":
        domain = str(payload.get("domain") or item.get("resource_key") or "").strip().lstrip("@")
        row.update(
            {
                "email": f"*@{domain}" if domain else email,
                "domain": domain,
                "imap_user": str(payload.get("imap_user") or ""),
                "imap_pass": str(payload.get("imap_pass") or ""),
                "imap_host": str(payload.get("imap_host") or ""),
                "imap_port": str(payload.get("imap_port") or ""),
            }
        )
    elif provider == "cfworker_admin_api":
        domain = str(payload.get("domain") or "").strip().lstrip("@")
        row.update(
            {
                "email": f"*@{domain}" if domain else email,
                "api_url": str(payload.get("api_url") or ""),
                "admin_token": str(payload.get("admin_token") or ""),
                "domain": domain,
                "fingerprint": str(payload.get("fingerprint") or ""),
            }
        )
    return row


def lease_email_mailboxes(
    *,
    provider: str = "icloud_api",
    count: int = 1,
    lease_id: str,
    db_path: str | Path | None = None,
) -> list[dict[str, str]]:
    provider = str(provider or "icloud_api").strip() or "icloud_api"
    want = max(1, min(500, int(count or 1)))
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for _ in range(want):
        item = lease_resource("email", provider, lease_id, db_path=db_path)
        key = str(item.get("resource_key") or "").strip()
        if not key or key in seen:
            break
        seen.add(key)
        rows.append(_email_lease_to_mailbox_row(item))
    return rows


def proxy_seed_sessions(
    *,
    provider: str = "proxy_seed",
    count: int = 1,
    region: str = "JP",
    ttl: int = 10,
    protocol: str = "socks5",
    db_path: str | Path | None = None,
) -> list[str]:
    _parse_seed, seed_from_payload, build_session = _proxy_seed_tools()
    want = max(1, min(500, int(count or 1)))
    with connect(db_path) as conn:
        _recover_expired_cooldowns(conn)
        rows = conn.execute(
            """
            SELECT * FROM resource_pool
            WHERE resource_type='proxy' AND provider=? AND status='available'
            ORDER BY success_count ASC, fail_count ASC, updated_at ASC, id ASC
            LIMIT ?
            """,
            (provider, want),
        ).fetchall()
        conn.commit()
    sessions: list[str] = []
    for row in rows[:want]:
        item = row_to_item(row)
        seed = seed_from_payload(item.get("payload"), resource_key=str(item.get("resource_key") or ""))
        sessions.append(build_session(seed, region=region, ttl=ttl, protocol=protocol).url)
    return sessions


def _recover_expired_cooldowns(conn: sqlite3.Connection) -> int:
    now = utc_now()
    cur = conn.execute(
        """
        UPDATE resource_pool
        SET status='available', cooldown_until='', lease_id='', updated_at=?
        WHERE status='cooldown' AND cooldown_until!='' AND cooldown_until<=?
        """,
        (now, now),
    )
    return int(cur.rowcount or 0)


def _recover_stale_leases(conn: sqlite3.Connection, lease_ttl_seconds: int = LEASE_TTL_SECONDS) -> int:
    threshold = (datetime.now(timezone.utc) - timedelta(seconds=max(60, int(lease_ttl_seconds or LEASE_TTL_SECONDS)))).isoformat()
    now = utc_now()
    cur = conn.execute(
        """
        UPDATE resource_pool
        SET status='available', lease_id='', updated_at=?, last_error='stale lease recovered'
        WHERE status='leased' AND updated_at<?
        """,
        (now, threshold),
    )
    return int(cur.rowcount or 0)


def recover_stale(lease_ttl_seconds: int = LEASE_TTL_SECONDS, db_path: str | Path | None = None) -> int:
    with connect(db_path) as conn:
        count = _recover_stale_leases(conn, lease_ttl_seconds)
        conn.commit()
        return count


def list_resources(
    resource_type: str = "",
    provider: str = "",
    status: str = "",
    limit: int = 2000,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    with connect(db_path) as conn:
        _recover_expired_cooldowns(conn)
        sql = "SELECT * FROM resource_pool WHERE 1=1"
        params: list[Any] = []
        if resource_type:
            sql += " AND resource_type=?"
            params.append(resource_type)
        if provider:
            sql += " AND provider=?"
            params.append(provider)
        if status and status != "all":
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(5000, int(limit or 2000))))
        rows = conn.execute(sql, tuple(params)).fetchall()
        counts = {
            str(row["status"]): int(row["c"] or 0)
            for row in conn.execute("SELECT status, COUNT(*) AS c FROM resource_pool GROUP BY status").fetchall()
        }
        conn.commit()
    return {"ok": True, "items": [row_to_item(row) for row in rows], "counts": counts}


def get_resource(resource_type: str, provider: str, resource_key: str, db_path: str | Path | None = None) -> dict[str, Any]:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM resource_pool WHERE resource_type=? AND provider=? AND resource_key=?",
            (resource_type, provider, resource_key),
        ).fetchone()
    return row_to_item(row) if row else {}


def upsert_resource(
    resource_type: str,
    provider: str,
    resource_key: str,
    payload: dict[str, Any],
    *,
    status: str = "available",
    error: str = "",
    db_path: str | Path | None = None,
) -> None:
    status = _normalize_status(status)
    now = utc_now()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO resource_pool(
              resource_type, provider, resource_key, payload_json, status,
              last_error, created_at, updated_at
            )
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(resource_type, provider, resource_key) DO UPDATE SET
              payload_json=excluded.payload_json,
              status=excluded.status,
              last_error=excluded.last_error,
              updated_at=excluded.updated_at
            """,
            (resource_type, provider, resource_key, dumps(payload), status, error, now, now),
        )
        conn.commit()


def lease_resource(
    resource_type: str,
    provider: str,
    lease_id: str,
    *,
    region: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    del region
    lease_id = str(lease_id or "").strip()
    if not lease_id:
        raise ValueError("lease_id is required")
    now = utc_now()
    with connect(db_path) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            _recover_expired_cooldowns(conn)
            _recover_stale_leases(conn)
            row = conn.execute(
                """
                SELECT * FROM resource_pool
                WHERE resource_type=? AND provider=?
                  AND (
                    status='available'
                    OR (status='leased' AND lease_id=?)
                  )
                ORDER BY
                  CASE WHEN lease_id=? THEN 0 ELSE 1 END,
                  success_count ASC,
                  fail_count ASC,
                  updated_at ASC,
                  id ASC
                LIMIT 1
                """,
                (resource_type, provider, lease_id, lease_id),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return {}
            conn.execute(
                """
                UPDATE resource_pool
                SET status='leased', lease_id=?, updated_at=?, last_error=''
                WHERE id=?
                """,
                (lease_id, now, int(row["id"])),
            )
            refreshed = conn.execute("SELECT * FROM resource_pool WHERE id=?", (int(row["id"]),)).fetchone()
            conn.execute("COMMIT")
            return row_to_item(refreshed)
        except Exception:
            conn.execute("ROLLBACK")
            raise


def set_status(resource_id: int, *, status: str, cooldown_until: str = "", error: str = "", db_path: str | Path | None = None) -> None:
    resource_id = int(resource_id or 0)
    if resource_id <= 0:
        return
    status = _normalize_status(status)
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE resource_pool
            SET status=?, cooldown_until=?, lease_id='', last_error=?, updated_at=?
            WHERE id=?
            """,
            (status, str(cooldown_until or ""), str(error or ""), utc_now(), resource_id),
        )
        conn.commit()


def set_status_many(ids: list[int], *, status: str, cooldown_until: str = "", error: str = "", db_path: str | Path | None = None) -> dict[str, Any]:
    normalized = [int(item) for item in ids if int(item or 0) > 0]
    if not normalized:
        return {"ok": False, "updated": 0}
    status = _normalize_status(status)
    placeholders = ",".join("?" for _ in normalized)
    with connect(db_path) as conn:
        conn.execute(
            f"""
            UPDATE resource_pool
            SET status=?, cooldown_until=?, lease_id='', last_error=?, updated_at=?
            WHERE id IN ({placeholders})
            """,
            (status, str(cooldown_until or ""), str(error or ""), utc_now(), *normalized),
        )
        conn.commit()
    return {"ok": True, "updated": len(normalized)}


def delete_many(ids: list[int], db_path: str | Path | None = None) -> dict[str, Any]:
    normalized = [int(item) for item in ids if int(item or 0) > 0]
    if not normalized:
        return {"ok": False, "deleted": 0}
    placeholders = ",".join("?" for _ in normalized)
    with connect(db_path) as conn:
        conn.execute(f"DELETE FROM resource_pool WHERE id IN ({placeholders})", tuple(normalized))
        conn.commit()
    return {"ok": True, "deleted": len(normalized)}


def report_resource(
    lease_id: str,
    resource_key: str,
    *,
    success: bool,
    cooldown_until: str = "",
    error: str = "",
    db_path: str | Path | None = None,
) -> None:
    lease_id = str(lease_id or "").strip()
    resource_key = str(resource_key or "").strip()
    if not lease_id or not resource_key:
        return
    next_status = "used" if success else ("cooldown" if cooldown_until else "available")
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE resource_pool
            SET status=?,
                lease_id='',
                success_count=success_count+?,
                fail_count=fail_count+?,
                cooldown_until=?,
                last_error=?,
                updated_at=?
            WHERE lease_id=? AND resource_key=?
            """,
            (
                next_status,
                1 if success else 0,
                0 if success else 1,
                "" if success else str(cooldown_until or ""),
                "" if success else str(error or ""),
                utc_now(),
                lease_id,
                resource_key,
            ),
        )
        conn.commit()


def _contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def classify_bind_phone_failure(reason: str, *, phase: str = "") -> tuple[str, str]:
    text = str(reason or "").strip()
    lowered = text.lower()
    phase = str(phase or "").strip().lower()
    if not lowered:
        if phase == "cleanup":
            return (BIND_PHONE_OUTCOME_RELEASED, BIND_PHONE_FAILURE_RELEASED)
        return (BIND_PHONE_OUTCOME_TRANSPORT_FAILED, BIND_PHONE_FAILURE_TRANSPORT)
    if BIND_PHONE_FAILURE_RECENTLY_USED in lowered or _contains_marker(lowered, PHONE_USED_MARKERS):
        return (BIND_PHONE_OUTCOME_RECENTLY_USED, BIND_PHONE_FAILURE_RECENTLY_USED)
    if BIND_PHONE_FAILURE_INVALID_NUMBER in lowered or _contains_marker(lowered, PHONE_INVALID_MARKERS):
        return (BIND_PHONE_OUTCOME_INVALID, BIND_PHONE_FAILURE_INVALID_NUMBER)
    if BIND_PHONE_FAILURE_TIMEOUT in lowered or _contains_marker(lowered, SMS_TIMEOUT_MARKERS):
        return (BIND_PHONE_OUTCOME_TIMEOUT, BIND_PHONE_FAILURE_TIMEOUT)
    if phase == "otp" and (BIND_PHONE_FAILURE_INVALID_OTP in lowered or _contains_marker(lowered, BIND_PHONE_OTP_INVALID_MARKERS)):
        return (BIND_PHONE_OUTCOME_INVALID, BIND_PHONE_FAILURE_INVALID_OTP)
    if phase == "otp" and _contains_marker(lowered, BIND_PHONE_STATUS_ZERO_MARKERS):
        return (BIND_PHONE_OUTCOME_OTP_SUBMIT_FAILED, BIND_PHONE_FAILURE_OTP_SUBMIT_STATUS_0)
    if phase == "otp" and (
        BIND_PHONE_FAILURE_OTP_SUBMIT_TRANSPORT in lowered
        or _contains_marker(lowered, NETWORK_MARKERS)
        or "transport" in lowered
        or "network" in lowered
    ):
        return (BIND_PHONE_OUTCOME_OTP_SUBMIT_FAILED, BIND_PHONE_FAILURE_OTP_SUBMIT_TRANSPORT)
    if phase == "otp" and _contains_marker(lowered, BIND_PHONE_OTP_SUBMIT_FAILURE_MARKERS):
        return (BIND_PHONE_OUTCOME_OTP_SUBMIT_FAILED, BIND_PHONE_FAILURE_OTP_SUBMIT_FAILED)
    if BIND_PHONE_FAILURE_TRANSPORT in lowered or _contains_marker(lowered, NETWORK_MARKERS) or "transport" in lowered or "network" in lowered:
        return (BIND_PHONE_OUTCOME_TRANSPORT_FAILED, BIND_PHONE_FAILURE_TRANSPORT)
    if phase == "send":
        return (BIND_PHONE_OUTCOME_TRANSPORT_FAILED, BIND_PHONE_FAILURE_TRANSPORT)
    if phase == "otp":
        return (BIND_PHONE_OUTCOME_OTP_SUBMIT_FAILED, BIND_PHONE_FAILURE_OTP_SUBMIT_FAILED)
    return (BIND_PHONE_OUTCOME_TRANSPORT_FAILED, BIND_PHONE_FAILURE_TRANSPORT)


def bind_phone_failure_resource_status(outcome: str, *, failure_code: str = "") -> tuple[str, int]:
    outcome = str(outcome or "").strip().lower()
    failure_code = str(failure_code or "").strip().lower()
    if outcome in {"", BIND_PHONE_OUTCOME_RELEASED}:
        return ("available", 0)
    if outcome == BIND_PHONE_OUTCOME_RECENTLY_USED:
        return ("used", 0)
    if outcome == BIND_PHONE_OUTCOME_INVALID and failure_code == BIND_PHONE_FAILURE_INVALID_NUMBER:
        return ("disabled", 0)
    if outcome in {
        BIND_PHONE_OUTCOME_INVALID,
        BIND_PHONE_OUTCOME_TIMEOUT,
        BIND_PHONE_OUTCOME_OTP_SUBMIT_FAILED,
        BIND_PHONE_OUTCOME_TRANSPORT_FAILED,
    }:
        return ("cooldown", BIND_PHONE_FAILURE_COOLDOWN_SECONDS)
    return ("available", 0)


class ResourcePoolService:
    def __init__(self, repo: Any | None = None):
        self.repo = repo or ResourcePoolRepository()

    def import_phone_urls(self, text: str, provider: str = "user_phone_url") -> int:
        entries = parse_phone_url_entries(text)
        rows = [(phone, {"phone": phone, "sms_url": sms_url}) for phone, sms_url in entries]
        return self.repo.import_many("phone", provider, rows)

    def import_proxy_seeds(self, text: str, *, protocol: str = "socks5", style: str = "") -> int:
        rows = parse_proxy_seed_entries(text, protocol=protocol, style=style)
        return self.repo.import_many("proxy", "proxy_seed", rows)

    def import_link_api_mailboxes(self, text: str, provider: str = "icloud_api") -> int:
        rows = parse_link_api_mailbox_entries(text)
        return self.repo.import_many("email", provider or "icloud_api", rows)

    def import_outlook_tokens(self, text: str, provider: str = "outlook_token") -> int:
        rows = parse_outlook_token_entries(text)
        return self.repo.import_many("email", provider or "outlook_token", rows)

    def import_icloud_privacy_mailboxes(self, text: str, provider: str = "icloud_privacy") -> int:
        rows = parse_icloud_privacy_entries(text)
        return self.repo.import_many("email", provider or "icloud_privacy", rows)

    def import_forwarded_domain_mailboxes(self, text: str, provider: str = "forwarded_domain") -> int:
        rows = parse_forwarded_domain_entries(text)
        return self.repo.import_many("email", provider or "forwarded_domain", rows)

    def import_cfworker_mailboxes(self, text: str, provider: str = "cfworker_admin_api") -> int:
        rows = parse_cfworker_entries(text)
        return self.repo.import_many("email", provider or "cfworker_admin_api", rows)

    def cooldown_until(self, seconds: int) -> str:
        return cooldown_until(seconds)


class ResourcePoolRepository:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = db_path
        with connect(db_path):
            pass

    def import_many(self, resource_type: str, provider: str, rows: list[tuple[str, dict[str, Any]]]) -> int:
        return import_many(resource_type, provider, rows, self.db_path)

    def list(self, resource_type: str = "", provider: str = "", status: str = "") -> list[dict[str, Any]]:
        return list_resources(resource_type, provider, status, db_path=self.db_path)["items"]

    def list_ids(self, resource_type: str = "", provider: str = "", status: str = "") -> list[int]:
        return [int(item.get("id") or 0) for item in self.list(resource_type, provider, status) if int(item.get("id") or 0) > 0]

    def get(self, resource_type: str, provider: str, resource_key: str) -> dict[str, Any]:
        return get_resource(resource_type, provider, resource_key, self.db_path)

    def upsert(self, resource_type: str, provider: str, resource_key: str, payload: dict[str, Any], *, status: str = "available", error: str = "") -> None:
        upsert_resource(resource_type, provider, resource_key, payload, status=status, error=error, db_path=self.db_path)

    def recover_stale(self, *, lease_ttl_seconds: int = LEASE_TTL_SECONDS) -> int:
        return recover_stale(lease_ttl_seconds=lease_ttl_seconds, db_path=self.db_path)

    def lease(self, resource_type: str, provider: str, lease_id: str, *, region: str = "") -> ResourceLease:
        return ResourceLease.from_dict(lease_resource(resource_type, provider, lease_id, region=region, db_path=self.db_path))

    def set_status(self, resource_id: int, *, status: str, cooldown_until: str = "", error: str = "") -> None:
        set_status(resource_id, status=status, cooldown_until=cooldown_until, error=error, db_path=self.db_path)

    def set_status_many(self, resource_ids: list[int], *, status: str, cooldown_until: str = "", error: str = "") -> int:
        return int(set_status_many(resource_ids, status=status, cooldown_until=cooldown_until, error=error, db_path=self.db_path).get("updated") or 0)

    def delete_many(self, resource_ids: list[int]) -> int:
        return int(delete_many(resource_ids, self.db_path).get("deleted") or 0)

    def report(self, lease_id: str, resource_key: str, *, success: bool, cooldown_until: str = "", error: str = "") -> None:
        report_resource(lease_id, resource_key, success=success, cooldown_until=cooldown_until, error=error, db_path=self.db_path)
