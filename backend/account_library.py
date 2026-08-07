from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .account_check import _extract_access_token, _parse_token_identity, check_account_eligibility, fetch_subscription_status_details
from . import resource_pool
from .go_email_protocol import verify_go_plus_batch, worker_supports_feature


DB_LOCK = threading.Lock()
INIT_DONE: set[str] = set()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def account_db_path() -> Path:
    configured = os.environ.get("UPISCAN_ACCOUNT_DB", "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / "data" / "accounts.sqlite3"


def connect() -> sqlite3.Connection:
    path = account_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
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
            CREATE TABLE IF NOT EXISTS account_library (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              account_key TEXT NOT NULL UNIQUE,
              account_id TEXT DEFAULT '',
              email TEXT DEFAULT '',
              password TEXT DEFAULT '',
              access_token TEXT DEFAULT '',
              session_json TEXT DEFAULT '',
              plan_type TEXT DEFAULT '',
              status TEXT DEFAULT 'active',
              source TEXT DEFAULT '',
              channels_json TEXT DEFAULT '[]',
              eligibility_status TEXT DEFAULT 'unknown',
              eligibility_reason TEXT DEFAULT '',
              eligibility_json TEXT DEFAULT '{}',
              last_checked_at TEXT DEFAULT '',
              health_status TEXT DEFAULT 'unknown',
              health_checked_at TEXT DEFAULT '',
              health_source TEXT DEFAULT '',
              health_error TEXT DEFAULT '',
              health_json TEXT DEFAULT '{}',
              note TEXT DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_account_library_status ON account_library(status);
            CREATE INDEX IF NOT EXISTS idx_account_library_email ON account_library(email);
            CREATE INDEX IF NOT EXISTS idx_account_library_eligibility ON account_library(eligibility_status);
            CREATE INDEX IF NOT EXISTS idx_account_library_health ON account_library(health_status);
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(account_library)").fetchall()}
        for column, definition in {
            "health_status": "TEXT DEFAULT 'unknown'",
            "health_checked_at": "TEXT DEFAULT ''",
            "health_source": "TEXT DEFAULT ''",
            "health_error": "TEXT DEFAULT ''",
            "health_json": "TEXT DEFAULT '{}'",
            "plus_status": "TEXT DEFAULT 'unknown'",
            "plus_verified_at": "TEXT DEFAULT ''",
            "plus_check_source": "TEXT DEFAULT ''",
            "plus_check_error": "TEXT DEFAULT ''",
            "plus_json": "TEXT DEFAULT '{}'",
        }.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE account_library ADD COLUMN {column} {definition}")
        conn.commit()
        INIT_DONE.add(key)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def token_preview(token: str) -> str:
    text = str(token or "").strip()
    if not text:
        return ""
    return text[:12] + "..." + text[-8:] if len(text) > 24 else text


def token_hash(token: str) -> str:
    text = str(token or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20] if text else ""


def row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    token = str(item.pop("access_token", "") or "")
    password = str(item.pop("password", "") or "")
    session_json = str(item.pop("session_json", "") or "")
    item["has_access_token"] = bool(token)
    item["access_token_preview"] = token_preview(token)
    item["has_password"] = bool(password)
    item["has_session_json"] = bool(session_json)
    item["channels"] = loads(item.pop("channels_json", ""), [])
    eligibility = loads(item.pop("eligibility_json", ""), {})
    item["eligibility"] = eligibility if isinstance(eligibility, dict) else {}
    health = loads(item.pop("health_json", ""), {})
    item["health"] = health if isinstance(health, dict) else {}
    plus = loads(item.pop("plus_json", ""), {})
    item["plus"] = plus if isinstance(plus, dict) else {}
    return item


def row_to_detail(row: sqlite3.Row) -> dict[str, Any]:
    item = row_to_summary(row)
    item["access_token"] = str(row["access_token"] or "")
    item["password"] = str(row["password"] or "")
    item["session_json"] = str(row["session_json"] or "")
    return item


def strip_account_secrets(item: dict[str, Any]) -> dict[str, Any]:
    item.pop("access_token", None)
    item.pop("password", None)
    item.pop("session_json", None)
    return item


def _session_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dict_value(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _str_value(value: Any) -> str:
    return str(value or "").strip()


def _session_json_record(value: dict[str, Any]) -> dict[str, Any]:
    user = _dict_value(value, "user")
    account = _dict_value(value, "account")
    token = _extract_access_token(json.dumps(value, ensure_ascii=False))
    record = {
        "session_json": dumps(value),
        "access_token": token,
        "email": _str_value(value.get("email") or user.get("email")),
        "account_id": _str_value(value.get("account_id") or value.get("accountId") or account.get("id")),
        "plan_type": _str_value(value.get("plan_type") or value.get("planType") or account.get("plan_type") or account.get("planType")),
        "password": _str_value(value.get("password") or value.get("generated_chatgpt_password")),
        "source": "session_json_import",
    }
    return {key: val for key, val in record.items() if val}


def _candidate_records_from_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        items: list[dict[str, Any]] = []
        for item in value:
            items.extend(_candidate_records_from_json(item))
        return items
    if not isinstance(value, dict):
        return []
    token = _extract_access_token(json.dumps(value, ensure_ascii=False))
    if token:
        return [_session_json_record(value)]
    records = value.get("accounts") or value.get("items") or value.get("data")
    if isinstance(records, list):
        return _candidate_records_from_json(records)
    return []


def _session_token_expires_at(session: dict[str, Any]) -> int:
    raw = _str_value(session.get("expires") or session.get("session_expires_at") or session.get("sessionExpiresAt"))
    if not raw:
        return -1
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except ValueError:
        return -1


def _build_chatgpt_storage_state(session: dict[str, Any]) -> dict[str, Any]:
    session_token = _str_value(session.get("sessionToken") or session.get("session_token"))
    if not session_token:
        return {}
    expires = _session_token_expires_at(session)
    cookies = []
    for domain in ("chatgpt.com", ".chatgpt.com"):
        cookies.append(
            {
                "name": "__Secure-next-auth.session-token",
                "value": session_token,
                "domain": domain,
                "path": "/",
                "expires": expires,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        )
    return {"cookies": cookies, "origins": []}


def _ensure_session_storage_state(record: dict[str, Any]) -> dict[str, Any]:
    session = _session_json(str(record.get("session_json") or ""))
    if not session:
        return record
    if _str_value(session.get("browser_storage_state_path") or session.get("oauth_browser_storage_state_path")):
        return record
    storage_state = _build_chatgpt_storage_state(session)
    if not storage_state:
        return record

    key_seed = _str_value(record.get("account_id") or record.get("email") or token_hash(str(record.get("access_token") or "")))
    filename_seed = hashlib.sha256(key_seed.encode("utf-8")).hexdigest()[:20] if key_seed else hashlib.sha256(dumps(session).encode("utf-8")).hexdigest()[:20]
    storage_path = Path.cwd() / "data" / "account_storage" / f"chatgpt_session_{filename_seed}.json"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_text(dumps(storage_state), encoding="utf-8")
    session["browser_storage_state_path"] = str(storage_path)
    session["browser_storage_state_source"] = "chatgpt_session_token"
    record["session_json"] = dumps(session)
    return record


def parse_account_import(text: str, default_channel: str = "") -> list[dict[str, Any]]:
    content = str(text or "").strip()
    if not content:
        return []
    records: list[dict[str, Any]] = []
    if content.startswith("[") or content.startswith("{"):
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        if parsed is not None:
            records.extend(_candidate_records_from_json(parsed))
    if not records:
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            record: dict[str, Any] = {}
            parts = [part.strip() for part in line.split("----")] if "----" in line else []
            if parts:
                email = next((part for part in parts if "@" in part), "")
                token = next((part for part in reversed(parts) if part.count(".") >= 2), "")
                password = parts[1] if len(parts) > 1 and parts[1] != token else ""
                record.update({"email": email, "password": password, "access_token": token or _extract_access_token(line)})
            elif "," in line and "@" in line:
                parts = [part.strip() for part in line.split(",")]
                record["email"] = next((part for part in parts if "@" in part), "")
                record["access_token"] = next((part for part in reversed(parts) if part.count(".") >= 2), "")
                record["password"] = parts[1] if len(parts) > 1 and parts[1] != record["access_token"] else ""
            else:
                session = _session_json(line)
                record["session_json"] = dumps(session) if session else ""
                record["access_token"] = _extract_access_token(line)
            records.append(record)
    normalized: list[dict[str, Any]] = []
    for record in records:
        token = str(record.get("access_token") or "").strip()
        if not token:
            continue
        identity = _parse_token_identity(token)
        email = str(record.get("email") or identity.email or "").strip()
        account_id = str(record.get("account_id") or identity.account_id or "").strip()
        key_seed = account_id or email or token_hash(token)
        channels = [default_channel.strip().lower()] if default_channel.strip() else []
        normalized_record = {
            "account_key": key_seed,
            "account_id": account_id,
            "email": email,
            "password": str(record.get("password") or "").strip(),
            "access_token": token,
            "session_json": str(record.get("session_json") or "").strip(),
            "plan_type": str(record.get("plan_type") or identity.plan_type or "").strip(),
            "status": str(record.get("status") or "active").strip().lower(),
            "source": str(record.get("source") or "manual_import").strip(),
            "channels": channels,
            "plus_status": str(
                record.get("plus_status")
                or ("verified_plus" if str(record.get("plan_type") or identity.plan_type or "").strip().lower().replace(" ", "") in PAID_PLANS else "unknown")
            ).strip(),
            "note": str(record.get("note") or "").strip(),
        }
        normalized.append(_ensure_session_storage_state(normalized_record))
    return normalized


def upsert_account(record: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect() as conn:
        existing = conn.execute("SELECT * FROM account_library WHERE account_key=?", (record["account_key"],)).fetchone()
        channels = record.get("channels") if isinstance(record.get("channels"), list) else []
        if existing:
            existing_channels = loads(existing["channels_json"], [])
            merged_channels = sorted({*(str(item) for item in existing_channels), *(str(item) for item in channels)} - {""})
            conn.execute(
                """
                UPDATE account_library
                SET account_id=?, email=?, password=COALESCE(NULLIF(?,''), password),
                    access_token=COALESCE(NULLIF(?,''), access_token),
                    session_json=COALESCE(NULLIF(?,''), session_json),
                    plan_type=COALESCE(NULLIF(?,''), plan_type), status=?,
                    source=COALESCE(NULLIF(?,''), source), channels_json=?,
                    plus_status=COALESCE(NULLIF(?,''), plus_status),
                    note=COALESCE(NULLIF(?,''), note),
                    updated_at=?
                WHERE account_key=?
                """,
                (
                    record.get("account_id") or existing["account_id"],
                    record.get("email") or existing["email"],
                    record.get("password") or "",
                    record.get("access_token") or "",
                    record.get("session_json") or "",
                    record.get("plan_type") or "",
                    record.get("status") or existing["status"] or "active",
                    record.get("source") or "",
                    dumps(merged_channels),
                    record.get("plus_status") or "",
                    record.get("note") or "",
                    now,
                    record["account_key"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO account_library(
                  account_key, account_id, email, password, access_token, session_json,
                  plan_type, status, source, channels_json, plus_status, note, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["account_key"],
                    record.get("account_id") or "",
                    record.get("email") or "",
                    record.get("password") or "",
                    record.get("access_token") or "",
                    record.get("session_json") or "",
                    record.get("plan_type") or "",
                    record.get("status") or "active",
                    record.get("source") or "",
                    dumps(channels),
                    record.get("plus_status") or "unknown",
                    record.get("note") or "",
                    now,
                    now,
                ),
            )
        conn.commit()
        saved = conn.execute("SELECT * FROM account_library WHERE account_key=?", (record["account_key"],)).fetchone()
        return row_to_summary(saved)


def import_accounts(text: str, default_channel: str = "") -> dict[str, Any]:
    records = parse_account_import(text, default_channel)
    imported: list[dict[str, Any]] = []
    for record in records:
        imported.append(upsert_account(record))
    return {"ok": True, "imported": len(imported), "items": imported}


def list_accounts(search: str = "", status: str = "active", eligibility: str = "", limit: int = 500) -> dict[str, Any]:
    sql = "SELECT * FROM account_library WHERE 1=1"
    params: list[Any] = []
    if status and status != "all":
        sql += " AND status=?"
        params.append(status)
    if eligibility and eligibility != "all":
        sql += " AND eligibility_status=?"
        params.append(eligibility)
    if search:
        sql += " AND (account_key LIKE ? OR account_id LIKE ? OR email LIKE ? OR note LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like, like])
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(max(1, min(2000, int(limit or 500))))
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM account_library").fetchone()["c"]
    return {"ok": True, "total": int(total), "items": [row_to_summary(row) for row in rows]}


def get_account(account_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM account_library WHERE id=?", (account_id,)).fetchone()
    return row_to_detail(row) if row else None


def set_status(ids: list[int], status: str) -> dict[str, Any]:
    ids = [int(item) for item in ids if int(item) > 0]
    if not ids:
        return {"ok": False, "updated": 0}
    placeholders = ",".join("?" for _ in ids)
    with connect() as conn:
        conn.execute(
            f"UPDATE account_library SET status=?, updated_at=? WHERE id IN ({placeholders})",
            (status, utc_now(), *ids),
        )
        conn.commit()
    return {"ok": True, "updated": len(ids)}


def update_account(account_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    account_id = int(account_id or 0)
    if account_id <= 0:
        return None
    allowed_statuses = {"active", "archived", "disabled"}
    with connect() as conn:
        existing = conn.execute("SELECT * FROM account_library WHERE id=?", (account_id,)).fetchone()
        if not existing:
            return None

        current_token = str(existing["access_token"] or "").strip()
        next_email = str(updates.get("email") if updates.get("email") is not None else existing["email"] or "").strip()
        next_password = str(updates.get("password") if updates.get("password") is not None else existing["password"] or "").strip()
        next_session_json = str(updates.get("session_json") if updates.get("session_json") is not None else existing["session_json"] or "").strip()
        explicit_token = updates.get("access_token")
        next_token = str(explicit_token if explicit_token is not None else current_token or "").strip()
        if next_session_json and explicit_token is None:
            extracted = _extract_access_token(next_session_json)
            if extracted:
                next_token = extracted

        identity = _parse_token_identity(next_token) if next_token else None
        next_account_id = str(existing["account_id"] or "").strip()
        next_plan_type = str(existing["plan_type"] or "").strip()
        if identity and identity.token_ok:
            next_email = next_email or str(identity.email or "").strip()
            next_account_id = str(identity.account_id or next_account_id or "").strip()
            next_plan_type = str(identity.plan_type or next_plan_type or "").strip()

        next_status = str(updates.get("status") if updates.get("status") is not None else existing["status"] or "active").strip().lower()
        if next_status not in allowed_statuses:
            next_status = "active"
        next_note = str(updates.get("note") if updates.get("note") is not None else existing["note"] or "").strip()
        token_changed = next_token != current_token
        now = utc_now()

        eligibility_status = str(existing["eligibility_status"] or "unknown")
        eligibility_reason = str(existing["eligibility_reason"] or "")
        eligibility_json = str(existing["eligibility_json"] or "{}")
        last_checked_at = str(existing["last_checked_at"] or "")
        health_status = str(existing["health_status"] or "unknown")
        health_checked_at = str(existing["health_checked_at"] or "")
        health_source = str(existing["health_source"] or "")
        health_error = str(existing["health_error"] or "")
        health_json = str(existing["health_json"] or "{}")
        plus_status = str(existing["plus_status"] or "unknown")
        plus_verified_at = str(existing["plus_verified_at"] or "")
        plus_check_source = str(existing["plus_check_source"] or "")
        plus_check_error = str(existing["plus_check_error"] or "")
        plus_json = str(existing["plus_json"] or "{}")
        if token_changed:
            eligibility_status = "unknown"
            eligibility_reason = ""
            eligibility_json = "{}"
            last_checked_at = ""
            health_status = "unknown"
            health_checked_at = ""
            health_source = ""
            health_error = ""
            health_json = "{}"
            plus_status = "unknown"
            plus_verified_at = ""
            plus_check_source = ""
            plus_check_error = ""
            plus_json = "{}"

        conn.execute(
            """
            UPDATE account_library
            SET account_id=?, email=?, password=?, access_token=?, session_json=?,
                plan_type=?, status=?, note=?,
                eligibility_status=?, eligibility_reason=?, eligibility_json=?, last_checked_at=?,
                health_status=?, health_checked_at=?, health_source=?, health_error=?, health_json=?,
                plus_status=?, plus_verified_at=?, plus_check_source=?, plus_check_error=?, plus_json=?,
                updated_at=?
            WHERE id=?
            """,
            (
                next_account_id,
                next_email,
                next_password,
                next_token,
                next_session_json,
                next_plan_type,
                next_status,
                next_note,
                eligibility_status,
                eligibility_reason,
                eligibility_json,
                last_checked_at,
                health_status,
                health_checked_at,
                health_source,
                health_error,
                health_json,
                plus_status,
                plus_verified_at,
                plus_check_source,
                plus_check_error,
                plus_json,
                now,
                account_id,
            ),
        )
        conn.commit()
        saved = conn.execute("SELECT * FROM account_library WHERE id=?", (account_id,)).fetchone()
        return row_to_summary(saved)


def delete_accounts(ids: list[int]) -> dict[str, Any]:
    ids = [int(item) for item in ids if int(item) > 0]
    if not ids:
        return {"ok": False, "deleted": 0}
    placeholders = ",".join("?" for _ in ids)
    with connect() as conn:
        conn.execute(f"DELETE FROM account_library WHERE id IN ({placeholders})", tuple(ids))
        conn.commit()
    return {"ok": True, "deleted": len(ids)}


def export_tokens(ids: list[int] | None = None, *, only_eligible: bool = False) -> dict[str, Any]:
    sql = "SELECT * FROM account_library WHERE status='active' AND access_token!=''"
    params: list[Any] = []
    if ids:
        normalized = [int(item) for item in ids if int(item) > 0]
        if normalized:
            sql += f" AND id IN ({','.join('?' for _ in normalized)})"
            params.extend(normalized)
    if only_eligible:
        sql += " AND eligibility_status='eligible'"
    sql += " ORDER BY updated_at DESC, id DESC"
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    tokens = [str(row["access_token"] or "").strip() for row in rows if str(row["access_token"] or "").strip()]
    return {"ok": True, "count": len(tokens), "text": "\n".join(tokens), "items": [row_to_summary(row) for row in rows]}


def _export_field(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").replace("----", " ").strip()


def export_import_text(ids: list[int] | None = None) -> dict[str, Any]:
    sql = "SELECT * FROM account_library WHERE status!='deleted' AND access_token!=''"
    params: list[Any] = []
    if ids:
        normalized = [int(item) for item in ids if int(item) > 0]
        if normalized:
            sql += f" AND id IN ({','.join('?' for _ in normalized)})"
            params.extend(normalized)
    sql += " ORDER BY updated_at DESC, id DESC"
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    lines = [
        "----".join(
            [
                _export_field(row["email"]),
                _export_field(row["password"]),
                _export_field(row["account_id"]),
                _export_field(row["access_token"]),
            ]
        )
        for row in rows
        if str(row["access_token"] or "").strip()
    ]
    return {"ok": True, "count": len(lines), "text": "\n".join(lines), "items": [row_to_summary(row) for row in rows]}


def check_accounts(ids: list[int], promo_id: str = "plus-1-month-free", concurrency: int = 3) -> dict[str, Any]:
    ids = [int(item) for item in ids if int(item) > 0]
    if not ids:
        return {"ok": False, "checked": 0, "items": []}
    placeholders = ",".join("?" for _ in ids)
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM account_library WHERE id IN ({placeholders})", tuple(ids)).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    ordered_rows = [by_id[item] for item in ids if item in by_id]
    workers = max(1, min(5, int(concurrency or 3), len(ordered_rows)))
    results: dict[int, dict[str, Any]] = {}

    def run(row: sqlite3.Row) -> tuple[int, dict[str, Any]]:
        token = str(row["access_token"] or "").strip()
        if not token:
            return int(row["id"]), {"token_ok": False, "eligible": False, "reason": "missing_access_token", "error": "missing access token"}
        return int(row["id"]), check_account_eligibility(token, promo_id)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, row) for row in ordered_rows]
        for future in as_completed(futures):
            account_id, result = future.result()
            results[account_id] = result

    now = utc_now()
    with connect() as conn:
        for account_id, result in results.items():
            status = "eligible" if result.get("eligible") else "not_eligible"
            if result.get("error") and result.get("reason") not in {"already_subscribed", "jwt_expired", "invalid_token"}:
                status = "failed"
            conn.execute(
                """
                UPDATE account_library
                SET eligibility_status=?, eligibility_reason=?, eligibility_json=?,
                    plan_type=COALESCE(NULLIF(?,''), plan_type), last_checked_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    str(result.get("reason") or result.get("error") or ""),
                    dumps(result),
                    str(result.get("plan_type") or ""),
                    now,
                    now,
                    account_id,
                ),
            )
        conn.commit()
    items = []
    for account_id in ids:
        if account_id in results:
            detail = get_account(account_id)
            if detail:
                detail["check_result"] = results[account_id]
                items.append(strip_account_secrets(detail))
    return {"ok": True, "checked": len(items), "items": items}


PAID_PLANS = {"plus", "pro", "premium", "paid", "team", "business", "enterprise", "chatgptplus", "chatgptpro", "chatgptteam"}


def check_token_health(token: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback or {}
    token = str(token or "").strip()
    key = str(fallback.get("account_key") or fallback.get("email") or fallback.get("id") or token_hash(token) or "unknown")
    if not token:
        return {
            "key": key,
            "ok": False,
            "health_status": "missing_material",
            "source": "local_jwt",
            "message": "missing access token",
        }
    identity = _parse_token_identity(token)
    if not identity.token_ok:
        return {
            "key": key,
            "ok": False,
            "health_status": "invalid_token",
            "source": "local_jwt",
            "message": "access token is not a valid JWT",
        }
    if identity.jwt_expired:
        return {
            "key": key,
            "ok": False,
            "health_status": "token_expired",
            "source": "local_jwt",
            "message": "access token expired",
            "jwt_exp_ms": identity.jwt_exp_ms,
            "jwt_exp_in_sec": identity.jwt_exp_in_sec,
            "email": identity.email,
            "account_id": identity.account_id,
            "plan_type": identity.plan_type,
        }
    if not identity.account_id and str(fallback.get("account_id") or "").strip():
        identity.account_id = str(fallback.get("account_id") or "").strip()
    if not identity.email and str(fallback.get("email") or "").strip():
        identity.email = str(fallback.get("email") or "").strip()
    subscription = fetch_subscription_status_details(identity)
    if subscription.get("ok"):
        plan = str(subscription.get("plan_type") or subscription.get("status") or "free").strip().lower().replace(" ", "") or "free"
        paid = plan in PAID_PLANS
        return {
            "key": key,
            "ok": True,
            "health_status": "active_plus" if paid else "active_free",
            "source": str(subscription.get("source") or "backend-api/wham/usage"),
            "message": "access token is valid and subscription API is reachable",
            "jwt_exp_ms": identity.jwt_exp_ms,
            "jwt_exp_in_sec": identity.jwt_exp_in_sec,
            "email": identity.email,
            "account_id": identity.account_id,
            "plan_type": plan,
            "subscription_status": str(subscription.get("status") or plan),
        }

    http_status = int(subscription.get("http_status") or 0)
    if http_status in {401, 403}:
        return {
            "key": key,
            "ok": False,
            "health_status": "invalid_token",
            "source": str(subscription.get("source") or "backend-api/wham/usage"),
            "message": str(subscription.get("error") or "access token rejected by subscription API"),
            "jwt_exp_ms": identity.jwt_exp_ms,
            "jwt_exp_in_sec": identity.jwt_exp_in_sec,
            "email": identity.email,
            "account_id": identity.account_id,
            "plan_type": identity.plan_type,
        }

    plan = str(identity.plan_type or fallback.get("plan_type") or "free").strip().lower().replace(" ", "") or "free"
    paid = plan in PAID_PLANS
    return {
        "key": key,
        "ok": True,
        "health_status": "active_plus" if paid else "active_free",
        "source": "local_jwt",
        "message": "access token JWT is parseable and not expired; subscription API check failed",
        "jwt_exp_ms": identity.jwt_exp_ms,
        "jwt_exp_in_sec": identity.jwt_exp_in_sec,
        "email": identity.email,
        "account_id": identity.account_id,
        "plan_type": plan,
        "subscription_check_error": str(subscription.get("error") or ""),
        "subscription_source": str(subscription.get("source") or "backend-api/wham/usage"),
    }


def _token_health(row: sqlite3.Row) -> dict[str, Any]:
    return check_token_health(
        str(row["access_token"] or "").strip(),
        {
            "id": row["id"],
            "account_key": row["account_key"],
            "email": row["email"],
            "account_id": row["account_id"],
            "plan_type": row["plan_type"],
        },
    )


def check_health(ids: list[int], concurrency: int = 8) -> dict[str, Any]:
    ids = [int(item) for item in ids if int(item) > 0]
    if not ids:
        return {"ok": False, "checked": 0, "counts": {}, "items": []}
    placeholders = ",".join("?" for _ in ids)
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM account_library WHERE id IN ({placeholders})", tuple(ids)).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    ordered_rows = [by_id[item] for item in ids if item in by_id]
    workers = max(1, min(16, int(concurrency or 8), len(ordered_rows)))
    results: dict[int, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_token_health, row): int(row["id"]) for row in ordered_rows}
        for future in as_completed(futures):
            account_id = futures[future]
            try:
                results[account_id] = future.result()
            except Exception as exc:
                results[account_id] = {
                    "key": str(account_id),
                    "ok": False,
                    "health_status": "unknown",
                    "source": "local_jwt",
                    "message": str(exc),
                }

    now = utc_now()
    with connect() as conn:
        for account_id, result in results.items():
            conn.execute(
                """
                UPDATE account_library
                SET health_status=?, health_checked_at=?, health_source=?, health_error=?,
                    health_json=?, plan_type=COALESCE(NULLIF(?,''), plan_type),
                    email=COALESCE(NULLIF(?,''), email), account_id=COALESCE(NULLIF(?,''), account_id),
                    updated_at=?
                WHERE id=?
                """,
                (
                    str(result.get("health_status") or "unknown"),
                    now,
                    str(result.get("source") or "local_jwt"),
                    "" if result.get("ok") else str(result.get("message") or ""),
                    dumps(result),
                    str(result.get("plan_type") or ""),
                    str(result.get("email") or ""),
                    str(result.get("account_id") or ""),
                    now,
                    account_id,
                ),
            )
        conn.commit()

    items = []
    counts: dict[str, int] = {}
    for account_id in ids:
        if account_id not in results:
            continue
        status = str(results[account_id].get("health_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        detail = get_account(account_id)
        if detail:
            detail["health_result"] = results[account_id]
            items.append(strip_account_secrets(detail))
    return {"ok": all(bool(result.get("ok")) for result in results.values()), "checked": len(items), "counts": counts, "items": items}


def mark_plus(ids: list[int]) -> dict[str, Any]:
    ids = [int(item) for item in ids if int(item) > 0]
    if not ids:
        return {"ok": False, "updated": 0, "items": []}
    placeholders = ",".join("?" for _ in ids)
    now = utc_now()
    result = {
        "ok": True,
        "paid": True,
        "plan_type": "plus",
        "plus_status": "manual_confirmed",
        "source": "manual",
        "checked_at": now,
    }
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE account_library
            SET plan_type='plus',
                plus_status='manual_confirmed',
                plus_verified_at=?,
                plus_check_source='manual',
                plus_check_error='',
                plus_json=?,
                updated_at=?
            WHERE id IN ({placeholders})
            """,
            (now, dumps(result), now, *ids),
        )
        conn.commit()
    items = [strip_account_secrets(item) for item in (get_account(item_id) for item_id in ids) if item]
    return {"ok": True, "updated": len(items), "items": items}


def _plus_proxy_pool(count: int, proxy_region: str = "JP") -> list[str]:
    proxy_region = str(proxy_region or "JP").strip().upper() or "JP"
    try:
        return resource_pool.proxy_seed_sessions(
            provider="proxy_seed",
            count=max(1, min(500, int(count or 1))),
            region=proxy_region,
            ttl=int(os.environ.get("ACCOUNT_PLUS_PROXY_TTL", "30") or 30),
            protocol=str(os.environ.get("ACCOUNT_PLUS_PROXY_PROTOCOL", "socks5") or "socks5"),
        )
    except Exception:
        return []


def _plus_proxy_for(proxies: list[str], index: int) -> str:
    if not proxies:
        return ""
    return proxies[index % len(proxies)]


def _verify_plus_row(row: sqlite3.Row, *, proxy: str = "") -> dict[str, Any]:
    token = str(row["access_token"] or "").strip()
    key = str(row["account_key"] or row["email"] or row["id"])
    if not token:
        return {
            "key": key,
            "ok": False,
            "paid": False,
            "plan_type": str(row["plan_type"] or ""),
            "plus_status": "check_failed",
            "source": "local_jwt",
            "message": "missing access token",
            "error_code": "missing_access_token",
        }
    identity = _parse_token_identity(token)
    if not identity.token_ok:
        return {
            "key": key,
            "ok": False,
            "paid": False,
            "plan_type": str(row["plan_type"] or ""),
            "plus_status": "banned",
            "source": "local_jwt",
            "message": "access token is not a valid JWT",
            "error_code": "invalid_token",
        }
    if identity.jwt_expired:
        return {
            "key": key,
            "ok": False,
            "paid": False,
            "plan_type": identity.plan_type or str(row["plan_type"] or ""),
            "plus_status": "check_failed",
            "source": "local_jwt",
            "message": "access token expired",
            "error_code": "token_expired",
            "jwt_exp_ms": identity.jwt_exp_ms,
            "jwt_exp_in_sec": identity.jwt_exp_in_sec,
            "email": identity.email,
            "account_id": identity.account_id,
        }

    subscription = fetch_subscription_status_details(identity, proxy=proxy)
    if subscription.get("ok"):
        plan = str(subscription.get("plan_type") or subscription.get("status") or "free").strip().lower().replace(" ", "") or "free"
        paid = plan in PAID_PLANS
        return {
            "key": key,
            "ok": True,
            "paid": paid,
            "plan_type": plan,
            "plus_status": "verified_plus" if paid else "free",
            "source": str(subscription.get("source") or "backend-api/wham/usage"),
            "message": "subscription API check succeeded",
            "http_status": int(subscription.get("http_status") or 200),
            "email": identity.email,
            "account_id": identity.account_id,
            "jwt_exp_ms": identity.jwt_exp_ms,
            "jwt_exp_in_sec": identity.jwt_exp_in_sec,
        }

    http_status = int(subscription.get("http_status") or 0)
    auth_failed = http_status in {401, 403}
    fallback_plan = str(identity.plan_type or row["plan_type"] or "").strip().lower().replace(" ", "")
    fallback_paid = fallback_plan in PAID_PLANS
    return {
        "key": key,
        "ok": False if auth_failed else bool(fallback_plan),
        "paid": fallback_paid,
        "plan_type": "banned" if auth_failed else (fallback_plan or "unknown"),
        "plus_status": "banned" if auth_failed else ("verified_plus" if fallback_paid else "check_failed"),
        "source": str(subscription.get("source") or "backend-api/wham/usage"),
        "message": str(subscription.get("error") or "subscription API check failed"),
        "error_code": "auth_failed" if auth_failed else "subscription_check_failed",
        "http_status": http_status or None,
        "email": identity.email,
        "account_id": identity.account_id,
        "jwt_exp_ms": identity.jwt_exp_ms,
        "jwt_exp_in_sec": identity.jwt_exp_in_sec,
    }


def _go_plus_result(row: sqlite3.Row, result: dict[str, Any]) -> dict[str, Any]:
    identity = _parse_token_identity(str(row["access_token"] or "").strip())
    paid = bool(result.get("paid"))
    plan = str(result.get("plan_type") or ("plus" if paid else "free")).strip().lower().replace(" ", "") or "free"
    status = "verified_plus" if paid else "free"
    if not result.get("ok") and str(result.get("error_code") or "") in {"invalid_item", "missing_access_token"}:
        status = "check_failed"
    if not result.get("ok") and str(result.get("status_code") or "") in {"401", "403"}:
        status = "banned"
    return {
        "key": str(result.get("key") or row["account_key"] or row["id"]),
        "ok": bool(result.get("ok")),
        "paid": paid,
        "plan_type": plan,
        "plus_status": status,
        "source": str(result.get("source") or "go-email-protocol/v2/plus-verify"),
        "message": str(result.get("message") or ""),
        "error_code": str(result.get("error_code") or ""),
        "http_status": result.get("status_code"),
        "email": identity.email,
        "account_id": identity.account_id or str(row["account_id"] or ""),
        "jwt_exp_ms": identity.jwt_exp_ms,
        "jwt_exp_in_sec": identity.jwt_exp_in_sec,
        "worker_result": result,
    }


def _verify_plus_with_go_worker(
    rows: list[sqlite3.Row],
    *,
    workers: int,
    proxy_region: str,
    use_proxy_pool: bool,
    go_email_protocol_url: str,
) -> tuple[dict[int, dict[str, Any]], list[str]] | None:
    config = {"go_email_protocol_url": go_email_protocol_url}
    if not worker_supports_feature("plus-verify", config):
        return None
    proxies = _plus_proxy_pool(max(len(rows), workers), proxy_region) if use_proxy_pool else []
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        item = {
            "key": str(row["account_key"] or row["email"] or row["id"]),
            "account_id": str(row["account_id"] or ""),
            "access_token": str(row["access_token"] or "").strip(),
        }
        proxy = _plus_proxy_for(proxies, index)
        if proxy:
            item["proxy"] = proxy
        items.append(item)
    payload = verify_go_plus_batch(items, config, workers=workers)
    raw_results = payload.get("results") if isinstance(payload.get("results"), list) else []
    results: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        raw = raw_results[index] if index < len(raw_results) and isinstance(raw_results[index], dict) else {}
        results[int(row["id"])] = _go_plus_result(row, raw)
    return results, proxies


def verify_plus(
    ids: list[int],
    *,
    concurrency: int = 8,
    proxy_region: str = "JP",
    use_proxy_pool: bool = True,
    go_email_protocol_url: str = "",
) -> dict[str, Any]:
    ids = [int(item) for item in ids if int(item) > 0]
    if not ids:
        return {"ok": False, "checked": 0, "paid": 0, "counts": {}, "items": []}
    placeholders = ",".join("?" for _ in ids)
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM account_library WHERE id IN ({placeholders})", tuple(ids)).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    ordered_rows = [by_id[item] for item in ids if item in by_id]
    workers = max(1, min(32, int(concurrency or 8), len(ordered_rows)))
    results: dict[int, dict[str, Any]] = {}
    proxies: list[str] = []

    go_url = str(go_email_protocol_url or os.environ.get("GO_EMAIL_PROTOCOL_URL") or "").strip()
    if go_url:
        try:
            go_result = _verify_plus_with_go_worker(
                ordered_rows,
                workers=workers,
                proxy_region=proxy_region,
                use_proxy_pool=use_proxy_pool,
                go_email_protocol_url=go_url,
            )
        except Exception:
            go_result = None
        if go_result is not None:
            results, proxies = go_result

    if not results:
        proxies = _plus_proxy_pool(max(len(ordered_rows), workers), proxy_region) if use_proxy_pool else []

        def run(index: int, row: sqlite3.Row) -> tuple[int, dict[str, Any]]:
            return int(row["id"]), _verify_plus_row(row, proxy=_plus_proxy_for(proxies, index))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run, index, row): int(row["id"]) for index, row in enumerate(ordered_rows)}
            for future in as_completed(futures):
                account_id = futures[future]
                try:
                    results[account_id] = future.result()[1]
                except Exception as exc:
                    results[account_id] = {
                        "key": str(account_id),
                        "ok": False,
                        "paid": False,
                        "plus_status": "check_failed",
                        "source": "backend-api/wham/usage",
                        "message": str(exc),
                        "error_code": "exception",
                    }

    now = utc_now()
    with connect() as conn:
        for account_id, result in results.items():
            plus_status = str(result.get("plus_status") or "check_failed")
            plan_type = str(result.get("plan_type") or "")
            health_status = "active_plus" if result.get("paid") else ("active_free" if plus_status == "free" else str(by_id[account_id]["health_status"] or "unknown"))
            conn.execute(
                """
                UPDATE account_library
                SET plus_status=?, plus_verified_at=?, plus_check_source=?, plus_check_error=?, plus_json=?,
                    plan_type=COALESCE(NULLIF(?,''), plan_type),
                    email=COALESCE(NULLIF(?,''), email), account_id=COALESCE(NULLIF(?,''), account_id),
                    health_status=CASE WHEN ?!='' THEN ? ELSE health_status END,
                    health_checked_at=CASE WHEN ?!='' THEN ? ELSE health_checked_at END,
                    health_source=CASE WHEN ?!='' THEN ? ELSE health_source END,
                    health_error=CASE WHEN ?!='' THEN '' ELSE health_error END,
                    health_json=CASE WHEN ?!='' THEN ? ELSE health_json END,
                    updated_at=?
                WHERE id=?
                """,
                (
                    plus_status,
                    now if plus_status in {"verified_plus", "manual_confirmed", "free", "banned"} else "",
                    str(result.get("source") or ""),
                    "" if result.get("ok") else str(result.get("message") or ""),
                    dumps(result),
                    plan_type,
                    str(result.get("email") or ""),
                    str(result.get("account_id") or ""),
                    health_status,
                    health_status,
                    health_status,
                    now,
                    health_status,
                    str(result.get("source") or ""),
                    health_status,
                    health_status,
                    dumps({**result, "health_status": health_status}),
                    now,
                    account_id,
                ),
            )
        conn.commit()

    items = []
    counts: dict[str, int] = {}
    for account_id in ids:
        if account_id not in results:
            continue
        status = str(results[account_id].get("plus_status") or "check_failed")
        counts[status] = counts.get(status, 0) + 1
        detail = get_account(account_id)
        if detail:
            detail["plus_result"] = results[account_id]
            items.append(strip_account_secrets(detail))
    return {
        "ok": all(bool(result.get("ok")) for result in results.values()),
        "checked": len(items),
        "paid": sum(1 for result in results.values() if bool(result.get("paid"))),
        "counts": counts,
        "items": items,
        "proxy_pool_used": bool(proxies),
        "proxy_region": str(proxy_region or "JP").strip().upper() or "JP",
    }


def export_json(ids: list[int] | None = None, *, include_secrets: bool = False) -> dict[str, Any]:
    sql = "SELECT * FROM account_library WHERE status!='deleted'"
    params: list[Any] = []
    if ids:
        normalized = [int(item) for item in ids if int(item) > 0]
        if normalized:
            sql += f" AND id IN ({','.join('?' for _ in normalized)})"
            params.extend(normalized)
    sql += " ORDER BY updated_at DESC, id DESC"
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    items = [row_to_detail(row) if include_secrets else row_to_summary(row) for row in rows]
    return {"ok": True, "count": len(items), "items": items, "text": dumps(items)}


def stats() -> dict[str, Any]:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM account_library").fetchone()["c"]
        active = conn.execute("SELECT COUNT(*) AS c FROM account_library WHERE status='active'").fetchone()["c"]
        eligible = conn.execute("SELECT COUNT(*) AS c FROM account_library WHERE eligibility_status='eligible'").fetchone()["c"]
        with_token = conn.execute("SELECT COUNT(*) AS c FROM account_library WHERE access_token!=''").fetchone()["c"]
        healthy = conn.execute("SELECT COUNT(*) AS c FROM account_library WHERE health_status IN ('active_free','active_plus','active')").fetchone()["c"]
        plus = conn.execute("SELECT COUNT(*) AS c FROM account_library WHERE plus_status IN ('verified_plus','manual_confirmed') OR health_status='active_plus' OR plan_type IN ('plus','pro','team','business','enterprise','paid')").fetchone()["c"]
    return {"ok": True, "total": int(total), "active": int(active), "eligible": int(eligible), "with_access_token": int(with_token), "healthy": int(healthy), "plus": int(plus)}
