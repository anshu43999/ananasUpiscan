from __future__ import annotations

import base64
import hashlib
import html as html_lib
import json
import os
import re
import secrets
import string
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from . import account_library, resource_pool
from .extractor.proxy import normalize_proxy_url, proxy_label
from .go_email_protocol import normalize_email_protocol_backend, run_go_email_protocol


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(max(12, length)))
        if (
            any(ch.islower() for ch in value)
            and any(ch.isupper() for ch in value)
            and any(ch.isdigit() for ch in value)
            and any(ch in "!@#$%^&*" for ch in value)
        ):
            return value


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * ((4 - len(part) % 4) % 4)
        payload = base64.urlsafe_b64decode(part.encode("ascii")).decode("utf-8")
        claims = json.loads(payload)
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def _extract_identity(token: str, fallback_email: str = "") -> tuple[str, str, str]:
    claims = _jwt_claims(token)
    namespaced = {
        str(name).rstrip("/").rsplit("/", 1)[-1]: value
        for name, value in claims.items()
        if str(name).startswith("https://") and isinstance(value, dict)
    }
    auth = namespaced.get("auth") or {}
    profile = namespaced.get("profile") or {}
    account_id = str(auth.get("chatgpt_account_id") or claims.get("sub") or "").strip()
    email = str(profile.get("email") or claims.get("email") or fallback_email).strip()
    plan_type = str(auth.get("chatgpt_plan_type") or "").strip()
    return account_id, email, plan_type


def extract_verification_code(text: str, expected_lengths: tuple[int, ...] = (6,)) -> str:
    cleaned = re.sub(r"https?://\S+", " ", str(text or ""))
    cleaned = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", " ", cleaned)
    lengths = "|".join(str(length) for length in expected_lengths)
    patterns = (
        rf"(?:verification|security|temporary|login|sign.?up)\s+code\D{{0,80}}(\d{{{lengths}}})",
        rf"code\s+is\D{{0,40}}(\d{{{lengths}}})",
        rf"\b(\d{{{lengths}}})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            return str(match.group(1))
    return ""


@dataclass
class MailboxAccount:
    email: str
    account_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class LinkApiMailbox:
    """邮箱接码适配器，兼容 email----收信URL----code:验证码API----mail:邮件API 格式。"""

    def __init__(self, order_text: str = "", proxy: str | None = None, poll_interval: int = 3):
        self.order_text = str(order_text or "")
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.poll_interval = max(1, int(poll_interval or 3))
        self.session = requests.Session()
        self.session.trust_env = False

    @staticmethod
    def _classify_api_link(value: str, label: str = "") -> str:
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

    def parse_row(self, text: str) -> dict[str, str] | None:
        parts = [part.strip() for part in str(text or "").strip().lstrip("\ufeff").split("----")]
        if len(parts) < 2 or "@" not in parts[0]:
            return None
        row = {"email": parts[0], "inbox_url": "", "code_url": "", "mail_url": ""}
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
            kind = self._classify_api_link(value, label)
            if kind == "code_url":
                row["code_url"] = value
            elif kind == "mail_url":
                row["mail_url"] = value
            elif kind == "inbox_url" and not row["inbox_url"]:
                row["inbox_url"] = value
        if not (row["inbox_url"] or row["code_url"] or row["mail_url"]):
            return None
        return row

    def rows(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for line in self.order_text.splitlines():
            parsed = self.parse_row(line)
            if parsed:
                rows.append(parsed)
        return rows

    def account_from_row(self, row: dict[str, str]) -> MailboxAccount:
        return MailboxAccount(email=row["email"], account_id=row["email"], extra={"provider_name": "icloud_api", **row})

    def _fetch_text(self, account: MailboxAccount) -> str:
        url = str((account.extra or {}).get("inbox_url") or "").strip()
        if not url:
            raise RuntimeError("邮箱缺少收信 URL")
        response = self.session.get(url, params={"n": 1, "_": int(time.time())}, proxies=self.proxies, timeout=15)
        if response.status_code >= 400:
            raise RuntimeError(f"收信 URL 请求失败: status={response.status_code}")
        return response.text or ""

    def _fetch_json_url(self, url: str, label: str) -> dict[str, Any]:
        response = self.session.get(url, params={"_": int(time.time())}, proxies=self.proxies, timeout=15)
        if response.status_code >= 400:
            raise RuntimeError(f"{label} API 请求失败: status={response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{label} API 未返回 JSON: {response.text[:180]}") from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    @staticmethod
    def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else payload

    @staticmethod
    def _code_marker(data: dict[str, Any]) -> str:
        code = str(data.get("code") or data.get("verification_code") or data.get("latest_verification_code") or "").strip()
        if not code:
            return ""
        parts = [
            str(data.get("message_id") or ""),
            str(data.get("mail_time") or data.get("latest_verification_message_date") or ""),
            str(data.get("updated_at") or ""),
            code,
        ]
        return "code-api:" + hashlib.sha1("|".join(parts).encode("utf-8", "ignore")).hexdigest()

    def _extract_code_from_payload(self, payload: dict[str, Any], code_pattern: str | None = None) -> tuple[str, str, bool]:
        data = self._payload_data(payload)
        code = str(data.get("code") or data.get("verification_code") or data.get("latest_verification_code") or "").strip()
        marker = self._code_marker(data)
        stale = bool(data.get("stale_code"))
        if code_pattern and code:
            match = re.search(code_pattern, code)
            code = (match.group(1) if match and match.groups() else match.group(0)) if match else ""
        if code and data.get("found") is not False and not stale:
            return code, marker, False
        return "", marker, stale

    @staticmethod
    def _is_poualiis_payload(data: dict[str, Any]) -> bool:
        return isinstance(data, dict) and "msg" in data and ("status" in data or "mailbox" in data or "time" in data)

    @staticmethod
    def _message_marker(mail: dict[str, Any]) -> str:
        raw_id = str(mail.get("message_id") or mail.get("id") or mail.get("emailId") or "").strip()
        if raw_id:
            return f"mail-api:{raw_id}"
        raw = json.dumps(mail, ensure_ascii=False, sort_keys=True)
        return "mail-api:" + hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()

    def _extract_code_from_mail_payload(self, payload: dict[str, Any], code_pattern: str | None = None) -> tuple[str, set[str]]:
        data = self._payload_data(payload)
        markers: set[str] = set()
        if self._is_poualiis_payload(data):
            status = data.get("status")
            msg = str(data.get("msg") or "").strip()
            if status is False or not msg:
                return "", set()
            marker = "mail-api:poualiis:" + hashlib.sha1(msg[:240].encode("utf-8", "ignore")).hexdigest()
            markers.add(marker)
            if code_pattern:
                match = re.search(code_pattern, msg)
                if match:
                    return match.group(1) if match.groups() else match.group(0), markers
            return extract_verification_code(msg, expected_lengths=(6,)), markers

        code = str(data.get("latest_verification_code") or "").strip()
        if code:
            markers.add(self._code_marker(data))
            return code, markers
        messages: list[dict[str, Any]] = []
        for key in ("messages", "archive_messages"):
            value = data.get(key)
            if isinstance(value, list):
                messages.extend(item for item in value if isinstance(item, dict))
        results = data.get("results")
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and isinstance(item.get("messages"), list):
                    messages.extend(msg for msg in item["messages"] if isinstance(msg, dict))
        for mail in messages:
            markers.add(self._message_marker(mail))
            raw = " ".join(str(mail.get(field) or "") for field in ("subject", "text", "content", "html", "body", "body_text", "body_preview", "snippet", "msg"))
            if code_pattern:
                match = re.search(code_pattern, raw)
                if match:
                    return match.group(1) if match.groups() else match.group(0), markers
            code = extract_verification_code(raw, expected_lengths=(6,))
            if code:
                return code, markers
        return "", markers

    def _extract_code_from_html(self, html: str, code_pattern: str | None = None) -> str:
        newest_card = re.search(r'(?is)<div class="card"[^>]*>(.*?)(?=<div class="card"|</body>|</html>|$)', html)
        source = newest_card.group(1) if newest_card else html
        text = re.sub(r"(?is)<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ", source)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = html_lib.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if code_pattern:
            match = re.search(code_pattern, text)
            if match:
                return match.group(1) if match.groups() else match.group(0)
        return extract_verification_code(text, expected_lengths=(6,))

    def get_current_ids(self, account: MailboxAccount) -> set[str]:
        markers: set[str] = set()
        code_url = str((account.extra or {}).get("code_url") or "").strip()
        if code_url:
            try:
                _code, marker, _stale = self._extract_code_from_payload(self._fetch_json_url(code_url, "code"))
                if marker:
                    markers.add(marker)
            except Exception:
                pass
        mail_url = str((account.extra or {}).get("mail_url") or "").strip()
        if mail_url:
            try:
                payload = self._fetch_json_url(mail_url, "mail")
                data = self._payload_data(payload)
                if self._is_poualiis_payload(data):
                    return set()
                _code, mail_markers = self._extract_code_from_mail_payload(payload)
                markers.update(mail_markers)
            except Exception:
                pass
        if markers:
            return markers
        try:
            text = self._fetch_text(account)
        except Exception:
            return set()
        return {hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()} if text.strip() else set()

    def wait_for_code(self, account: MailboxAccount, *, timeout: int = 180, before_ids: set[str] | None = None, code_pattern: str | None = None) -> str:
        seen = set(before_ids or set())
        deadline = time.time() + timeout
        code_url = str((account.extra or {}).get("code_url") or "").strip()
        mail_url = str((account.extra or {}).get("mail_url") or "").strip()
        inbox_url = str((account.extra or {}).get("inbox_url") or "").strip()
        last_error = ""
        last_poualiis_marker = ""
        last_poualiis_code = ""
        while time.time() < deadline:
            if code_url:
                try:
                    code, marker, stale = self._extract_code_from_payload(self._fetch_json_url(code_url, "code"), code_pattern=code_pattern)
                    if marker and marker not in seen and code:
                        return code
                    if marker and (stale or not code):
                        seen.add(marker)
                except Exception as exc:
                    last_error = str(exc).splitlines()[0][:160]
            if mail_url:
                try:
                    payload = self._fetch_json_url(mail_url, "mail")
                    data = self._payload_data(payload)
                    code, markers = self._extract_code_from_mail_payload(payload, code_pattern=code_pattern)
                    if self._is_poualiis_payload(data):
                        marker = next(iter(markers), "")
                        if code and (
                            (not last_poualiis_marker and not last_poualiis_code)
                            or (marker and marker != last_poualiis_marker)
                            or (code and code != last_poualiis_code)
                        ):
                            last_poualiis_marker = marker
                            last_poualiis_code = code
                            return code
                    else:
                        fresh_markers = markers - seen
                        if code and fresh_markers:
                            return code
                        seen.update(markers)
                except Exception as exc:
                    last_error = str(exc).splitlines()[0][:160]
            if inbox_url:
                try:
                    text = self._fetch_text(account)
                    marker = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
                    if marker not in seen:
                        code = self._extract_code_from_html(text, code_pattern=code_pattern)
                        if code:
                            return code
                        seen.add(marker)
                except Exception as exc:
                    last_error = str(exc).splitlines()[0][:160]
            time.sleep(self.poll_interval)
        detail = f"; last_error={last_error}" if last_error else ""
        raise TimeoutError(f"等待邮箱验证码超时({timeout}s){detail}")


@dataclass
class EmailRegistrationJob:
    job_id: str
    status: str = "pending"
    total: int = 0
    completed: int = 0
    success: int = 0
    failed: int = 0
    logs: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "total": self.total,
            "completed": self.completed,
            "success": self.success,
            "failed": self.failed,
            "logs": list(self.logs[-300:]),
            "items": list(self.items),
            "error": self.error or None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class EmailRegistrationManager:
    def __init__(self) -> None:
        self._jobs: dict[str, EmailRegistrationJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(os.environ.get("UPISCAN_EMAIL_REGISTER_WORKERS", "2") or 2)))

    def create_job(self, payload: dict[str, Any]) -> EmailRegistrationJob:
        job_id = uuid.uuid4().hex
        job = EmailRegistrationJob(job_id=job_id)
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run_job, job_id, payload)
        return job

    def get_job(self, job_id: str) -> EmailRegistrationJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _log(self, job_id: str, message: str, level: str = "info") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.logs.append({"timestamp": utc_now(), "message": message, "level": level})
            job.updated_at = utc_now()

    def _patch(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in updates.items():
                setattr(job, key, value)
            job.updated_at = utc_now()

    def _append_item(self, job_id: str, item: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.items.append(item)
            job.completed += 1
            if item.get("ok"):
                job.success += 1
            else:
                job.failed += 1
            job.updated_at = utc_now()

    def _run_job(self, job_id: str, payload: dict[str, Any]) -> None:
        try:
            rows = self._mailbox_rows_from_payload(job_id, payload)
            if not rows:
                raise RuntimeError("没有可用邮箱行，请使用 email----收信URL 或 email----code:...----mail:... 格式")
            proxy_pool = self._registration_proxy_pool(payload, len(rows))
            concurrency = max(1, min(8, int(payload.get("concurrency") or 1), len(rows)))
            self._patch(job_id, status="running", total=len(rows))
            self._log(job_id, f"邮箱注册任务开始：{len(rows)} 个邮箱，并发 {concurrency}")
            if proxy_pool:
                self._log(job_id, f"注册 IP 池已加载：{len(proxy_pool)} 条，每个邮箱按顺序轮换，失败后切换下一条")
            else:
                self._log(job_id, "注册 IP 池为空：将使用直连或参考注册器默认代理配置", "warn")
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(self._register_one, job_id, row, index + 1, payload, proxy_pool)
                    for index, row in enumerate(rows)
                ]
                for future in as_completed(futures):
                    self._append_item(job_id, future.result())
            final = "completed" if self.get_job(job_id) and self.get_job(job_id).failed == 0 else "failed"
            self._patch(job_id, status=final)
            self._log(job_id, f"邮箱注册任务结束：成功 {self.get_job(job_id).success if self.get_job(job_id) else 0}，失败 {self.get_job(job_id).failed if self.get_job(job_id) else 0}")
        except Exception as exc:
            self._patch(job_id, status="failed", error=str(exc))
            self._log(job_id, str(exc), "error")

    def _mailbox_rows_from_payload(self, job_id: str, payload: dict[str, Any]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        if bool(payload.get("use_email_resource_pool")):
            count = int(payload.get("email_resource_count") or 1)
            provider = str(payload.get("email_resource_provider") or "icloud_api").strip() or "icloud_api"
            if provider == "__unsupported__":
                raise RuntimeError("iCloud Privacy email resources can be managed, but automatic registration OTP is not supported yet.")
            if provider not in {"icloud_api", "outlook_token", "icloud_privacy", "forwarded_domain", "cfworker_admin_api"}:
                self._log(job_id, f"邮箱资源池 provider={provider} 已可导入管理；当前注册执行器仅直接支持 icloud_api 接码行", "warn")
            leased = resource_pool.lease_email_mailboxes(provider=provider, count=count, lease_id=job_id)
            if not leased:
                raise RuntimeError(f"邮箱资源池没有可租用资源：provider={provider}")
            rows.extend(leased)
            self._log(job_id, f"邮箱资源池已租用：{len(leased)} 条，provider={provider}")

        mailbox_text = str(payload.get("mailbox_text") or "")
        if mailbox_text.strip():
            proxy = str(payload.get("mailbox_proxy") or "").strip() or None
            poll_interval = int(payload.get("email_otp_poll_interval") or 3)
            provider = str(payload.get("email_resource_provider") or "icloud_api").strip() or "icloud_api"
            if provider == "outlook_token":
                for key, data in resource_pool.parse_outlook_token_entries(mailbox_text):
                    rows.append(
                        {
                            "email": str(data.get("email") or key),
                            "password": str(data.get("password") or ""),
                            "client_id": str(data.get("client_id") or ""),
                            "refresh_token": str(data.get("refresh_token") or ""),
                            "_resource_key": "",
                            "_resource_provider": "outlook_token",
                            "_resource_lease_id": "",
                        }
                    )
            elif provider == "icloud_privacy":
                for key, data in resource_pool.parse_icloud_privacy_entries(mailbox_text):
                    rows.append(
                        {
                            "email": str(data.get("email") or key),
                            "imap_user": str(data.get("imap_user") or ""),
                            "imap_pass": str(data.get("imap_pass") or ""),
                            "imap_host": str(data.get("imap_host") or ""),
                            "imap_port": str(data.get("imap_port") or ""),
                            "_resource_key": "",
                            "_resource_provider": "icloud_privacy",
                            "_resource_lease_id": "",
                        }
                    )
            elif provider == "forwarded_domain":
                for key, data in resource_pool.parse_forwarded_domain_entries(mailbox_text):
                    rows.append(
                        {
                            "email": f"*@{key}",
                            "domain": str(data.get("domain") or key),
                            "imap_user": str(data.get("imap_user") or ""),
                            "imap_pass": str(data.get("imap_pass") or ""),
                            "imap_host": str(data.get("imap_host") or ""),
                            "imap_port": str(data.get("imap_port") or ""),
                            "_resource_key": "",
                            "_resource_provider": "forwarded_domain",
                            "_resource_lease_id": "",
                        }
                    )
            elif provider == "cfworker_admin_api":
                for key, data in resource_pool.parse_cfworker_entries(mailbox_text):
                    rows.append(
                        {
                            "email": f"*@{data.get('domain') or key}",
                            "api_url": str(data.get("api_url") or ""),
                            "admin_token": str(data.get("admin_token") or ""),
                            "domain": str(data.get("domain") or ""),
                            "fingerprint": str(data.get("fingerprint") or ""),
                            "_resource_key": "",
                            "_resource_provider": "cfworker_admin_api",
                            "_resource_lease_id": "",
                        }
                    )
            else:
                rows.extend(LinkApiMailbox(mailbox_text, proxy=proxy, poll_interval=poll_interval).rows())
        return rows

    @staticmethod
    def _registration_proxy_pool(payload: dict[str, Any], target_count: int = 1) -> list[str]:
        raw_items: list[str] = []
        if bool(payload.get("use_proxy_resource_pool")):
            retry_attempts = max(1, min(5, int(payload.get("registration_retry_attempts") or 1)))
            count = int(payload.get("proxy_resource_count") or 0)
            if count <= 0:
                count = max(1, int(target_count or 1) * retry_attempts)
            raw_items.extend(
                resource_pool.proxy_seed_sessions(
                    provider=str(payload.get("proxy_resource_provider") or "proxy_seed"),
                    count=count,
                    region=str(payload.get("proxy_seed_region") or "JP"),
                    ttl=int(payload.get("proxy_seed_ttl") or 10),
                    protocol=str(payload.get("proxy_seed_protocol") or "socks5"),
                )
            )
        raw_many = payload.get("registration_proxies")
        if isinstance(raw_many, list):
            raw_items.extend(str(item or "") for item in raw_many)
        else:
            raw_items.extend(re.split(r"[\r\n,]+", str(raw_many or "")))
        legacy = str(payload.get("registration_proxy") or "").strip()
        if legacy:
            raw_items.append(legacy)

        proxies: list[str] = []
        seen: set[str] = set()
        for raw in raw_items:
            text = str(raw or "").strip()
            if not text or text.startswith("#"):
                continue
            normalized = normalize_proxy_url(text, "http")
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            proxies.append(normalized)
        return proxies

    @staticmethod
    def _proxy_for_attempt(proxy_pool: list[str], index: int, attempt_no: int) -> str:
        if not proxy_pool:
            return ""
        offset = (max(1, index) - 1) + (max(1, attempt_no) - 1)
        return proxy_pool[offset % len(proxy_pool)]

    def _register_one(self, job_id: str, row: dict[str, str], index: int, payload: dict[str, Any], proxy_pool: list[str]) -> dict[str, Any]:
        email = row["email"]
        resource_key = str(row.get("_resource_key") or "").strip()
        password = str(payload.get("chatgpt_password") or "").strip() or generate_password()
        max_attempts = max(1, min(5, int(payload.get("registration_retry_attempts") or 1)))
        last_error = ""
        used_labels: list[str] = []
        self._log(job_id, f"[{index}] 开始注册 {email}")
        for attempt_no in range(1, max_attempts + 1):
            selected_proxy = self._proxy_for_attempt(proxy_pool, index, attempt_no)
            selected_label = proxy_label(selected_proxy)
            used_labels.append(selected_label)
            config = dict(payload.get("config") or {})
            config.update(
                {
                    "mailbox_provider": str(row.get("_resource_provider") or "icloud_api"),
                    "icloud_api_order_text": self._row_to_text(row),
                    "icloud_api_email": email,
                    "outlook_email": email,
                    "outlook_password": str(row.get("password") or ""),
                    "outlook_client_id": str(row.get("client_id") or ""),
                    "outlook_refresh_token": str(row.get("refresh_token") or ""),
                    "icloud_privacy_order_text": str(row.get("email") or ""),
                    "icloud_privacy_email": str(row.get("email") or ""),
                    "mailbox_domain": str(row.get("domain") or ""),
                    "mailbox_imap_user": str(row.get("imap_user") or ""),
                    "mailbox_imap_pass": str(row.get("imap_pass") or ""),
                    "mailbox_imap_host": str(row.get("imap_host") or ""),
                    "mailbox_imap_port": str(row.get("imap_port") or ""),
                    "cfworker_api_url": str(row.get("api_url") or ""),
                    "cfworker_admin_token": str(row.get("admin_token") or ""),
                    "cfworker_domain": str(row.get("domain") or ""),
                    "cfworker_fingerprint": str(row.get("fingerprint") or ""),
                    "chatgpt_password": password,
                    "email_otp_timeout": int(payload.get("email_otp_timeout") or 200),
                    "email_otp_poll_interval": int(payload.get("email_otp_poll_interval") or 3),
                    "headed": bool(payload.get("headed", False)),
                    "email_register_flow": str(payload.get("email_register_flow") or "fast"),
                    "browser_engine": str(payload.get("browser_engine") or "playwright"),
                    "email_protocol_backend": str(payload.get("email_protocol_backend") or "python"),
                    "go_email_protocol_url": str(payload.get("go_email_protocol_url") or ""),
                    "go_email_protocol_timeout_seconds": int(payload.get("go_email_protocol_timeout_seconds") or 900),
                    "go_email_protocol_poll_interval_ms": int(payload.get("go_email_protocol_poll_interval_ms") or 1000),
                }
            )
            if str(row.get("_resource_provider") or "icloud_api") not in {"icloud_api", "outlook_token", "icloud_privacy", "forwarded_domain", "cfworker_admin_api"}:
                config["force_reference_email_register"] = True
            if selected_proxy:
                config["proxy"] = selected_proxy
            self._log(job_id, f"[{index}] 第 {attempt_no}/{max_attempts} 次注册 {email}，代理 {selected_label}")
            try:
                result = self._run_reference_registration(config, headed=bool(payload.get("headed", False)), task_id=f"{job_id}-{index}-{attempt_no}")
                token = str(result.get("access_token") or result.get("chatgpt_access_token_initial") or "").strip()
                if not token:
                    raise RuntimeError("注册器未返回 access_token")
                account_id, resolved_email, plan_type = _extract_identity(token, fallback_email=email)
                stored_result = dict(result)
                stored_result["registration_proxy_label"] = selected_label
                saved = account_library.upsert_account(
                    {
                        "account_key": account_id or resolved_email or email,
                        "account_id": account_id,
                        "email": resolved_email or email,
                        "password": str(result.get("password") or password),
                        "access_token": token,
                        "session_json": json.dumps(stored_result, ensure_ascii=False),
                        "plan_type": plan_type or str(result.get("plan_type") or "free"),
                        "status": "active",
                        "source": "email_registration",
                        "channels": [],
                        "note": f"邮箱注册导入；注册代理 {selected_label}",
                    }
                )
                self._log(job_id, f"[{index}] 注册成功 {resolved_email or email}，代理 {selected_label}")
                if resource_key:
                    resource_pool.report_resource(job_id, resource_key, success=True)
                return {
                    "ok": True,
                    "email": resolved_email or email,
                    "account_id": account_id,
                    "account": saved,
                    "proxy_label": selected_label,
                    "attempts": attempt_no,
                }
            except Exception as exc:
                last_error = str(exc)
                level = "warn" if attempt_no < max_attempts else "error"
                self._log(job_id, f"[{index}] 第 {attempt_no}/{max_attempts} 次失败 {email}，代理 {selected_label}: {exc}", level)
        if resource_key:
            resource_pool.report_resource(
                job_id,
                resource_key,
                success=False,
                cooldown_until=resource_pool.cooldown_until(60 * 60),
                error=last_error[:500],
            )
        return {
            "ok": False,
            "email": email,
            "error": last_error,
            "proxy_label": used_labels[-1] if used_labels else "direct",
            "attempts": len(used_labels) or max_attempts,
            "tried_proxy_labels": used_labels,
        }

    @staticmethod
    def _row_to_text(row: dict[str, str]) -> str:
        parts = [row.get("email", "")]
        if row.get("inbox_url"):
            parts.append(row["inbox_url"])
        if row.get("code_url"):
            parts.append(f"code:{row['code_url']}")
        if row.get("mail_url"):
            parts.append(f"mail:{row['mail_url']}")
        return "----".join(parts)

    def _run_reference_registration(self, config: dict[str, Any], *, headed: bool, task_id: str) -> dict[str, Any]:
        if normalize_email_protocol_backend(config.get("email_protocol_backend")) == "go":
            return self._run_go_protocol_registration(config, task_id=task_id)

        builtin_error = ""
        if not config.get("force_reference_email_register"):
            try:
                return self._run_builtin_registration(config, headed=headed, task_id=task_id)
            except Exception as exc:
                builtin_error = str(exc)
                if config.get("disable_reference_email_register_fallback"):
                    raise
                self._log(task_id.split("-", 1)[0], f"内置邮箱注册执行器失败，尝试参考项目回退: {exc}", "warn")

        reference_root = str(
            config.get("reference_root")
            or os.environ.get("UPISCAN_EMAIL_REGISTER_REFERENCE_ROOT")
            or ""
        ).strip()
        if not reference_root or not Path(reference_root).exists():
            detail = f": {builtin_error}" if builtin_error else ""
            raise RuntimeError(f"邮箱注册执行器不可用：内置执行器失败，且未设置 UPISCAN_EMAIL_REGISTER_REFERENCE_ROOT 回退路径{detail}")
        if reference_root not in sys.path:
            sys.path.insert(0, reference_root)
        try:
            from registration.email_register import EmailRegistrationOrchestrator  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"无法加载参考注册器，请安装浏览器注册依赖: {exc}") from exc
        logs: list[str] = []

        def log_fn(message: str) -> None:
            logs.append(str(message))

        runner = EmailRegistrationOrchestrator(log_fn=log_fn)
        result = runner.run(config, headed=headed, task_id=task_id)
        if logs:
            result = dict(result or {})
            result["runner_logs"] = logs[-120:]
        return result if isinstance(result, dict) else {}

    def _run_go_protocol_registration(self, config: dict[str, Any], *, task_id: str) -> dict[str, Any]:
        runtime_root = Path(__file__).resolve().parent / "registration_runtime"
        runtime_path = str(runtime_root)
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)

        provider = str(config.get("mailbox_provider") or "icloud_api").strip().lower()
        log_job_id = task_id.split("-", 1)[0]
        password = str(config.get("chatgpt_password") or "").strip() or generate_password()
        otp_timeout = int(config.get("email_otp_timeout") or 200)
        config = dict(config)

        if provider == "outlook_token":
            from core.mailbox.outlook_token import OutlookTokenMailbox  # type: ignore

            mailbox = OutlookTokenMailbox(config, log_fn=lambda message: self._log(log_job_id, str(message)))
            account = mailbox.first(str(config.get("outlook_email") or ""), include_used=True)
            email = account.email
            config["outlook_email"] = account.email
            config["outlook_password"] = account.password
            config["outlook_client_id"] = account.client_id
            config["outlook_refresh_token"] = account.refresh_token
            rejected_codes: set[str] = set()

            def otp_callback() -> str:
                code = mailbox.wait_for_openai_code(
                    account,
                    timeout=otp_timeout,
                    not_before=datetime.now(timezone.utc) - timedelta(seconds=30),
                    reject_codes=rejected_codes,
                )
                if code:
                    rejected_codes.add(code)
                return code

        elif provider == "icloud_api":
            mailbox = LinkApiMailbox(
                str(config.get("icloud_api_order_text") or ""),
                proxy=str(config.get("mailbox_proxy") or "") or None,
                poll_interval=int(config.get("email_otp_poll_interval") or 3),
            )
            rows = mailbox.rows()
            if not rows:
                raise RuntimeError("Go protocol registration missing iCloud API mailbox row.")
            account = mailbox.account_from_row(rows[0])
            email = str(config.get("icloud_api_email") or account.email).strip() or account.email
            before_ids = set(mailbox.get_current_ids(account) or set())

            def otp_callback() -> str:
                code = mailbox.wait_for_code(account, timeout=otp_timeout, before_ids=before_ids)
                if code:
                    try:
                        before_ids.update(mailbox.get_current_ids(account) or set())
                    except Exception:
                        pass
                return code

        elif provider in {"icloud_privacy", "forwarded_domain", "cfworker_admin_api"}:
            from core.mailbox.forwarded_domain import ForwardedDomainMailbox  # type: ignore
            from core.mailbox.providers import CFWorkerMailbox, ICloudPrivacyMailbox  # type: ignore

            if provider == "icloud_privacy":
                mailbox = ICloudPrivacyMailbox.from_config(config)
                account = mailbox.account_for_email(str(config.get("icloud_privacy_email") or config.get("email") or ""))
            elif provider == "forwarded_domain":
                mailbox = ForwardedDomainMailbox.from_config(config)
                account = mailbox.create_account()
            else:
                mailbox = CFWorkerMailbox.from_config(config)
                account = mailbox.create_account()
            email = account.email
            before_ids = set(mailbox.get_current_ids(account) or set())

            def otp_callback() -> str:
                code = mailbox.wait_for_code(account, timeout=otp_timeout, before_ids=before_ids)
                if code:
                    try:
                        before_ids.update(mailbox.get_current_ids(account) or set())
                    except Exception:
                        pass
                return code

        else:
            raise RuntimeError(f"Go protocol registration unsupported mailbox_provider: {provider}")

        result = run_go_email_protocol(
            config,
            email=email,
            password=password,
            otp_callback=otp_callback,
            task_id=task_id,
            log=lambda message: self._log(log_job_id, str(message)),
        )
        result["mailbox_provider"] = provider
        return result

    def _run_builtin_registration(self, config: dict[str, Any], *, headed: bool, task_id: str) -> dict[str, Any]:
        runtime_root = Path(__file__).resolve().parent / "registration_runtime"
        if not runtime_root.exists():
            raise RuntimeError("内置邮箱注册运行时缺失")
        runtime_path = str(runtime_root)
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)

        try:
            from core.browser.session import BrowserSession, extract_chatgpt_access_token  # type: ignore
            from platforms.chatgpt.fast_email_register import FastEmailRegistrationFlow  # type: ignore
            from platforms.chatgpt.browser_register import _browser_registration_flow, _get_cookies  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"内置邮箱注册运行时加载失败，请确认浏览器依赖已安装: {exc}") from exc

        mailbox_provider = str(config.get("mailbox_provider") or "icloud_api").strip() or "icloud_api"
        if mailbox_provider == "outlook_token":
            return self._run_builtin_outlook_registration(config, headed=headed, task_id=task_id)
        if mailbox_provider in {"icloud_privacy", "forwarded_domain", "cfworker_admin_api"}:
            return self._run_builtin_provider_registration(config, headed=headed, task_id=task_id)

        mailbox = LinkApiMailbox(
            str(config.get("icloud_api_order_text") or ""),
            proxy=str(config.get("mailbox_proxy") or "") or None,
            poll_interval=int(config.get("email_otp_poll_interval") or 3),
        )
        rows = mailbox.rows()
        if not rows:
            raise RuntimeError("内置邮箱注册缺少邮箱接码行")
        account = mailbox.account_from_row(rows[0])
        email = str(config.get("icloud_api_email") or account.email).strip() or account.email
        password = str(config.get("chatgpt_password") or "").strip() or generate_password()
        timeout = int(config.get("email_register_timeout") or 600)
        otp_timeout = int(config.get("email_otp_timeout") or 200)
        config = dict(config)
        config["headed"] = bool(headed or config.get("headed", False))
        config.setdefault("browser_engine", "playwright")
        config.setdefault("browser_profile_mode", "per_task")
        config.setdefault("browser_profile_dir", str(Path.cwd() / "data" / "browser_profiles" / "email_register" / task_id))
        config["_force_fresh_browser_context"] = True
        config["_log_fn"] = lambda message: self._log(task_id.split("-", 1)[0], str(message))

        before_ids: set[str] = set()
        try:
            before_ids = set(mailbox.get_current_ids(account) or set())
            self._log(task_id.split("-", 1)[0], f"邮箱验证码等待基线: {len(before_ids)} 封历史邮件")
        except Exception as exc:
            self._log(task_id.split("-", 1)[0], f"邮箱验证码基线获取失败，继续注册: {str(exc).splitlines()[0][:160]}", "warn")

        def otp_callback() -> str:
            code = mailbox.wait_for_code(account, timeout=otp_timeout, before_ids=before_ids)
            if code:
                try:
                    before_ids.update(mailbox.get_current_ids(account) or set())
                except Exception:
                    pass
            return code

        flow = str(config.get("email_register_flow") or "fast").strip().lower()
        with BrowserSession(config) as session:
            if flow == "fast":
                flow_result = FastEmailRegistrationFlow(log_fn=lambda message: self._log(task_id.split("-", 1)[0], str(message))).run(
                    session,
                    email=email,
                    password=password,
                    otp_callback=otp_callback,
                    timeout=timeout,
                )
                access_token = str(flow_result.get("access_token") or "")
                cookies = flow_result.get("cookies") if isinstance(flow_result.get("cookies"), dict) else {}
                state = flow_result.get("state") if isinstance(flow_result.get("state"), dict) else {}
            else:
                state = _browser_registration_flow(session.page, email, password, otp_callback, None, lambda message: self._log(task_id.split("-", 1)[0], str(message)))
                token_result = extract_chatgpt_access_token(session.page, attempts=30, delay=2.0, log_fn=lambda message: self._log(task_id.split("-", 1)[0], str(message)))
                access_token = token_result.access_token if token_result.success else ""
                if not access_token:
                    raise RuntimeError(f"邮箱注册已完成但 access_token 提取失败: {token_result.failure_reason or token_result.status}")
                cookies = _get_cookies(session.page)
            account_id, resolved_email, plan_type = _extract_identity(access_token, fallback_email=email)
            storage_path = Path.cwd() / "data" / "registered_accounts" / f"storage_{task_id}.json"
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_state = session.save_storage_state(str(storage_path))
        session_token = str((cookies or {}).get("__Secure-next-auth.session-token") or (cookies or {}).get("__Secure-authjs.session-token") or "")
        return {
            "success": True,
            "status": "email_registered",
            "stage": "manual_plus_required",
            "registration_mode": "email",
            "registration_status": "registered",
            "task_id": task_id,
            "email": resolved_email or email,
            "account_id": account_id,
            "password": password,
            "generated_chatgpt_password": password,
            "plan_type": plan_type or "free",
            "access_token": access_token,
            "chatgpt_access_token_initial": access_token,
            "session_token": session_token,
            "browser_engine": str(config.get("browser_engine") or ""),
            "browser_profile_dir": str(config.get("browser_profile_dir") or ""),
            "browser_storage_state_path": storage_state,
            "email_register_flow": flow,
            "state": state,
        }

    def _run_builtin_provider_registration(self, config: dict[str, Any], *, headed: bool, task_id: str) -> dict[str, Any]:
        try:
            from core.browser.session import BrowserSession, extract_chatgpt_access_token  # type: ignore
            from core.mailbox.forwarded_domain import ForwardedDomainMailbox  # type: ignore
            from core.mailbox.providers import CFWorkerMailbox, ICloudPrivacyMailbox  # type: ignore
            from platforms.chatgpt.browser_register import _browser_registration_flow, _get_cookies  # type: ignore
            from platforms.chatgpt.fast_email_register import FastEmailRegistrationFlow  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"Built-in mailbox provider registration runtime failed to load: {exc}") from exc

        log_job_id = task_id.split("-", 1)[0]
        provider = str(config.get("mailbox_provider") or "").strip().lower()
        config = dict(config)
        config["headed"] = bool(headed or config.get("headed", False))
        config.setdefault("browser_engine", "playwright")
        config.setdefault("browser_profile_mode", "per_task")
        config.setdefault("browser_profile_dir", str(Path.cwd() / "data" / "browser_profiles" / "email_register" / task_id))
        config["_force_fresh_browser_context"] = True
        config["_log_fn"] = lambda message: self._log(log_job_id, str(message))

        if provider == "icloud_privacy":
            mailbox = ICloudPrivacyMailbox.from_config(config)
            account = mailbox.account_for_email(str(config.get("icloud_privacy_email") or config.get("email") or ""))
        elif provider == "forwarded_domain":
            mailbox = ForwardedDomainMailbox.from_config(config)
            account = mailbox.create_account()
        elif provider == "cfworker_admin_api":
            mailbox = CFWorkerMailbox.from_config(config)
            account = mailbox.create_account()
        else:
            raise RuntimeError(f"Unsupported mailbox provider: {provider}")

        email = account.email
        password = str(config.get("chatgpt_password") or "").strip() or generate_password()
        timeout = int(config.get("email_register_timeout") or 600)
        otp_timeout = int(config.get("email_otp_timeout") or 200)
        flow = str(config.get("email_register_flow") or "fast").strip().lower()

        before_ids: set[str] = set()
        try:
            before_ids = set(mailbox.get_current_ids(account) or set())
            self._log(log_job_id, f"Mailbox OTP baseline loaded: provider={provider}, messages={len(before_ids)}")
        except Exception as exc:
            self._log(log_job_id, f"Mailbox OTP baseline failed, continuing: {str(exc).splitlines()[0][:160]}", "warn")

        def otp_callback() -> str:
            code = mailbox.wait_for_code(account, timeout=otp_timeout, before_ids=before_ids)
            if code:
                try:
                    before_ids.update(mailbox.get_current_ids(account) or set())
                except Exception:
                    pass
            return code

        with BrowserSession(config) as session:
            if flow == "fast":
                flow_result = FastEmailRegistrationFlow(log_fn=lambda message: self._log(log_job_id, str(message))).run(
                    session,
                    email=email,
                    password=password,
                    otp_callback=otp_callback,
                    timeout=timeout,
                )
                access_token = str(flow_result.get("access_token") or "")
                cookies = flow_result.get("cookies") if isinstance(flow_result.get("cookies"), dict) else {}
                state = flow_result.get("state") if isinstance(flow_result.get("state"), dict) else {}
            else:
                state = _browser_registration_flow(session.page, email, password, otp_callback, None, lambda message: self._log(log_job_id, str(message)))
                token_result = extract_chatgpt_access_token(session.page, attempts=30, delay=2.0, log_fn=lambda message: self._log(log_job_id, str(message)))
                access_token = token_result.access_token if token_result.success else ""
                if not access_token:
                    raise RuntimeError(f"Email registration finished but access_token extraction failed: {token_result.failure_reason or token_result.status}")
                cookies = _get_cookies(session.page)
            account_id, resolved_email, plan_type = _extract_identity(access_token, fallback_email=email)
            storage_path = Path.cwd() / "data" / "registered_accounts" / f"storage_{task_id}.json"
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_state = session.save_storage_state(str(storage_path))

        session_token = str((cookies or {}).get("__Secure-next-auth.session-token") or (cookies or {}).get("__Secure-authjs.session-token") or "")
        return {
            "success": True,
            "status": "email_registered",
            "stage": "manual_plus_required",
            "registration_mode": "email",
            "registration_status": "registered",
            "task_id": task_id,
            "email": resolved_email or email,
            "account_id": account_id,
            "password": password,
            "generated_chatgpt_password": password,
            "plan_type": plan_type or "free",
            "access_token": access_token,
            "chatgpt_access_token_initial": access_token,
            "session_token": session_token,
            "browser_engine": str(config.get("browser_engine") or ""),
            "browser_profile_dir": str(config.get("browser_profile_dir") or ""),
            "browser_storage_state_path": storage_state,
            "email_register_flow": flow,
            "mailbox_provider": provider,
            "state": state,
        }

    def _run_builtin_outlook_registration(self, config: dict[str, Any], *, headed: bool, task_id: str) -> dict[str, Any]:
        try:
            from core.browser.session import BrowserSession, extract_chatgpt_access_token  # type: ignore
            from core.mailbox.outlook_token import OutlookTokenMailbox  # type: ignore
            from platforms.chatgpt.browser_register import _browser_registration_flow, _get_cookies  # type: ignore
            from platforms.chatgpt.fast_email_register import FastEmailRegistrationFlow  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"Built-in Outlook token registration runtime failed to load: {exc}") from exc

        log_job_id = task_id.split("-", 1)[0]
        config = dict(config)
        config["headed"] = bool(headed or config.get("headed", False))
        config.setdefault("browser_engine", "playwright")
        config.setdefault("browser_profile_mode", "per_task")
        config.setdefault("browser_profile_dir", str(Path.cwd() / "data" / "browser_profiles" / "email_register" / task_id))
        config["_force_fresh_browser_context"] = True
        config["_log_fn"] = lambda message: self._log(log_job_id, str(message))

        mailbox = OutlookTokenMailbox(config, log_fn=lambda message: self._log(log_job_id, str(message)))
        account = mailbox.first(str(config.get("outlook_email") or ""), include_used=True)
        email = account.email
        password = str(config.get("chatgpt_password") or "").strip() or generate_password()
        timeout = int(config.get("email_register_timeout") or 600)
        otp_timeout = int(config.get("email_otp_timeout") or 200)
        flow = str(config.get("email_register_flow") or "fast").strip().lower()
        rejected_codes: set[str] = set()

        def otp_callback() -> str:
            started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
            code = mailbox.wait_for_openai_code(
                account,
                timeout=otp_timeout,
                not_before=started_at,
                reject_codes=rejected_codes,
            )
            if code:
                rejected_codes.add(code)
            return code

        with BrowserSession(config) as session:
            if flow == "fast":
                flow_result = FastEmailRegistrationFlow(log_fn=lambda message: self._log(log_job_id, str(message))).run(
                    session,
                    email=email,
                    password=password,
                    otp_callback=otp_callback,
                    timeout=timeout,
                )
                access_token = str(flow_result.get("access_token") or "")
                cookies = flow_result.get("cookies") if isinstance(flow_result.get("cookies"), dict) else {}
                state = flow_result.get("state") if isinstance(flow_result.get("state"), dict) else {}
            else:
                state = _browser_registration_flow(session.page, email, password, otp_callback, None, lambda message: self._log(log_job_id, str(message)))
                token_result = extract_chatgpt_access_token(session.page, attempts=30, delay=2.0, log_fn=lambda message: self._log(log_job_id, str(message)))
                access_token = token_result.access_token if token_result.success else ""
                if not access_token:
                    raise RuntimeError(f"Email registration finished but access_token extraction failed: {token_result.failure_reason or token_result.status}")
                cookies = _get_cookies(session.page)
            account_id, resolved_email, plan_type = _extract_identity(access_token, fallback_email=email)
            storage_path = Path.cwd() / "data" / "registered_accounts" / f"storage_{task_id}.json"
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_state = session.save_storage_state(str(storage_path))

        mailbox.mark_used(email, "registered")
        session_token = str((cookies or {}).get("__Secure-next-auth.session-token") or (cookies or {}).get("__Secure-authjs.session-token") or "")
        return {
            "success": True,
            "status": "email_registered",
            "stage": "manual_plus_required",
            "registration_mode": "email",
            "registration_status": "registered",
            "task_id": task_id,
            "email": resolved_email or email,
            "account_id": account_id,
            "password": password,
            "generated_chatgpt_password": password,
            "plan_type": plan_type or "free",
            "access_token": access_token,
            "chatgpt_access_token_initial": access_token,
            "session_token": session_token,
            "browser_engine": str(config.get("browser_engine") or ""),
            "browser_profile_dir": str(config.get("browser_profile_dir") or ""),
            "browser_storage_state_path": storage_state,
            "email_register_flow": flow,
            "mailbox_provider": "outlook_token",
            "state": state,
        }


email_registration_manager = EmailRegistrationManager()
