from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DB_LOCK = threading.Lock()
INIT_DONE: set[str] = set()
PBKDF2_ITERATIONS = 240_000
TOKEN_TTL_SECONDS = int(os.environ.get("UPISCAN_AUTH_TOKEN_TTL_SECONDS", str(60 * 60 * 24 * 30)) or (60 * 60 * 24 * 30))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def auth_db_path() -> Path:
    configured = os.environ.get("UPISCAN_AUTH_DB", "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / "data" / "auth.sqlite3"


def _secret_path() -> Path:
    configured = os.environ.get("UPISCAN_AUTH_SECRET_FILE", "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / "data" / "auth.secret"


def _load_secret() -> bytes:
    env_secret = os.environ.get("UPISCAN_AUTH_SECRET", "").strip()
    if env_secret:
        return env_secret.encode("utf-8")
    path = _secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return raw.encode("utf-8")
    raw = secrets.token_urlsafe(48)
    path.write_text(raw, encoding="utf-8")
    return raw.encode("utf-8")


def connect() -> sqlite3.Connection:
    path = auth_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
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
            CREATE TABLE IF NOT EXISTS system_users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'admin',
              status TEXT NOT NULL DEFAULT 'active',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_login_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_system_users_username ON system_users(username);
            CREATE INDEX IF NOT EXISTS idx_system_users_status ON system_users(status);
            """
        )
        conn.commit()
        INIT_DONE.add(key)


def user_count() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM system_users").fetchone()
    return int(row["c"] if row else 0)


def has_admin() -> bool:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM system_users WHERE role='admin'").fetchone()
    return int(row["c"] if row else 0) > 0


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * ((4 - len(data) % 4) % 4))


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${_b64url(salt)}${_b64url(digest)}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations_raw, salt_raw, digest_raw = str(stored or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        expected = _b64url_decode(digest_raw)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64url_decode(salt_raw), int(iterations_raw))
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)


def _public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "username": str(row["username"] or ""),
        "role": str(row["role"] or "admin"),
        "status": str(row["status"] or "active"),
        "created_at": str(row["created_at"] or ""),
        "last_login_at": str(row["last_login_at"] or ""),
    }


def register_first_admin(username: str, password: str) -> dict[str, Any]:
    username = str(username or "").strip()
    password = str(password or "")
    if len(username) < 3:
        raise ValueError("用户名至少需要 3 个字符")
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    with DB_LOCK:
        with connect() as conn:
            existing = conn.execute("SELECT COUNT(*) AS c FROM system_users").fetchone()
            if int(existing["c"] if existing else 0) > 0:
                raise PermissionError("系统已完成初始化，不再开放注册")
            now = utc_now()
            conn.execute(
                """
                INSERT INTO system_users(username, password_hash, role, status, created_at, updated_at, last_login_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (username, _password_hash(password), "admin", "active", now, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM system_users WHERE username=?", (username,)).fetchone()
    user = _public_user(row)
    return {"user": user, "token": create_token(user)}


def login(username: str, password: str) -> dict[str, Any]:
    username = str(username or "").strip()
    password = str(password or "")
    with connect() as conn:
        row = conn.execute("SELECT * FROM system_users WHERE username=?", (username,)).fetchone()
        if not row or str(row["status"] or "") != "active" or not _verify_password(password, str(row["password_hash"] or "")):
            raise PermissionError("用户名或密码错误")
        now = utc_now()
        conn.execute("UPDATE system_users SET last_login_at=?, updated_at=? WHERE id=?", (now, now, int(row["id"])))
        conn.commit()
        row = conn.execute("SELECT * FROM system_users WHERE id=?", (int(row["id"]),)).fetchone()
    user = _public_user(row)
    return {"user": user, "token": create_token(user)}


def create_token(user: dict[str, Any]) -> str:
    payload = {
        "sub": int(user["id"]),
        "username": str(user["username"]),
        "role": str(user.get("role") or "admin"),
        "iat": int(time.time()),
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    payload_raw = _b64url(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_load_secret(), payload_raw.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_raw}.{_b64url(sig)}"


def verify_token(token: str) -> dict[str, Any] | None:
    text = str(token or "").strip()
    if "." not in text:
        return None
    payload_raw, sig_raw = text.rsplit(".", 1)
    expected = hmac.new(_load_secret(), payload_raw.encode("ascii"), hashlib.sha256).digest()
    try:
        actual = _b64url_decode(sig_raw)
        payload = json.loads(_b64url_decode(payload_raw).decode("utf-8"))
    except Exception:
        return None
    if not hmac.compare_digest(actual, expected):
        return None
    if int(payload.get("exp") or 0) <= int(time.time()):
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM system_users WHERE id=? AND status='active'", (int(payload.get("sub") or 0),)).fetchone()
    return _public_user(row) if row else None


def auth_status() -> dict[str, Any]:
    return {"initialized": user_count() > 0, "registration_open": user_count() == 0}
