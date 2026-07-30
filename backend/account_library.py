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

from .account_check import _extract_access_token, _parse_token_identity, check_account_eligibility


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
    return item


def row_to_detail(row: sqlite3.Row) -> dict[str, Any]:
    item = row_to_summary(row)
    item["access_token"] = str(row["access_token"] or "")
    item["password"] = str(row["password"] or "")
    item["session_json"] = str(row["session_json"] or "")
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
        return [{"session_json": dumps(value), "access_token": token}]
    records = value.get("accounts") or value.get("items") or value.get("data")
    if isinstance(records, list):
        return _candidate_records_from_json(records)
    return []


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
        normalized.append(
            {
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
                "note": str(record.get("note") or "").strip(),
            }
        )
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
                    source=COALESCE(NULLIF(?,''), source), channels_json=?, note=COALESCE(NULLIF(?,''), note),
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
                  plan_type, status, source, channels_json, note, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                items.append(detail)
    return {"ok": True, "checked": len(items), "items": items}


PAID_PLANS = {"plus", "pro", "premium", "paid", "team", "business", "enterprise", "chatgptplus", "chatgptpro", "chatgptteam"}


def _token_health(row: sqlite3.Row) -> dict[str, Any]:
    token = str(row["access_token"] or "").strip()
    key = str(row["account_key"] or row["email"] or row["id"])
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
    plan = str(identity.plan_type or row["plan_type"] or "free").strip().lower().replace(" ", "") or "free"
    paid = plan in PAID_PLANS
    return {
        "key": key,
        "ok": True,
        "health_status": "active_plus" if paid else "active_free",
        "source": "local_jwt",
        "message": "access token JWT is parseable and not expired",
        "jwt_exp_ms": identity.jwt_exp_ms,
        "jwt_exp_in_sec": identity.jwt_exp_in_sec,
        "email": identity.email,
        "account_id": identity.account_id,
        "plan_type": plan,
    }


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
            items.append(detail)
    return {"ok": all(bool(result.get("ok")) for result in results.values()), "checked": len(items), "counts": counts, "items": items}


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
    return {"ok": True, "total": int(total), "active": int(active), "eligible": int(eligible), "with_access_token": int(with_token), "healthy": int(healthy)}
