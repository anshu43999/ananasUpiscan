from __future__ import annotations

import base64
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from . import account_library, resource_pool
from .extractor.proxy import normalize_proxy_url, proxy_label
from .job_retention import env_int, prune_jobs, trim_sequence
from .registration_proxy_precheck import filter_clean_proxies


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
        claims = json.loads(base64.urlsafe_b64decode(part.encode("ascii")).decode("utf-8"))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def _extract_identity(token: str) -> tuple[str, str, str]:
    claims = _jwt_claims(token)
    namespaced = {
        str(name).rstrip("/").rsplit("/", 1)[-1]: value
        for name, value in claims.items()
        if str(name).startswith("https://") and isinstance(value, dict)
    }
    auth = namespaced.get("auth") or {}
    profile = namespaced.get("profile") or {}
    return (
        str(auth.get("chatgpt_account_id") or claims.get("sub") or "").strip(),
        str(profile.get("email") or claims.get("email") or "").strip(),
        str(auth.get("chatgpt_plan_type") or "").strip(),
    )


def _extract_sms_code(text: str, *, ignored_numbers: set[str] | None = None) -> str:
    raw = str(text or "")
    ignored = {"".join(ch for ch in value if ch.isdigit()) for value in (ignored_numbers or set()) if value}
    try:
        payload = json.loads(raw)
        candidates: list[str] = []
        if isinstance(payload, dict):
            for key in ("code", "verification_code", "otp", "text", "sms", "message", "msg", "content"):
                value = payload.get(key)
                if value is not None:
                    candidates.append(str(value))
            data = payload.get("data")
            if isinstance(data, dict):
                for key in ("code", "verification_code", "otp", "text", "sms", "message", "msg", "content"):
                    value = data.get(key)
                    if value is not None:
                        candidates.append(str(value))
                phone = str(data.get("phoneNumber") or data.get("phone") or "")
                if phone:
                    ignored.add("".join(ch for ch in phone if ch.isdigit()))
            if not candidates:
                candidates.append(json.dumps(payload, ensure_ascii=False))
        else:
            candidates = [raw]
    except Exception:
        candidates = [raw]
    for candidate in candidates:
        for match in re.finditer(r"\b(\d{4,8})\b", candidate):
            code = match.group(1)
            if code not in ignored:
                return code
    return ""


@dataclass
class PhoneSmsEntry:
    phone: str
    sms_url: str


class UserProvidedPhoneSmsPool:
    def __init__(self, text: str, *, proxy: str | None = None, country_code: str = "", poll_interval: int = 3):
        self.entries = self.parse_entries(text)
        self.country_code = "".join(ch for ch in str(country_code or "") if ch.isdigit())
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.poll_interval = max(1, int(poll_interval or 3))
        self.session = requests.Session()
        self.session.trust_env = False
        self._ignored_codes_by_url: dict[str, set[str]] = {}

    @staticmethod
    def parse_entries(value: str | list[str]) -> list[PhoneSmsEntry]:
        rows = value if isinstance(value, list) else str(value or "").replace("\r", "\n").split("\n")
        entries: list[PhoneSmsEntry] = []
        for row in rows:
            line = str(row or "").strip()
            if not line or line.startswith("#"):
                continue
            normalized = line.replace("----", "|")
            if "|" not in normalized:
                raise RuntimeError(f"手机号接码行格式错误，期望 phone|sms_url 或 phone----sms_url: {line[:80]}")
            parts = [part.strip() for part in normalized.split("|") if part.strip()]
            phone = parts[0] if parts else ""
            sms_url = next((part for part in parts[1:] if part.startswith(("http://", "https://"))), parts[1] if len(parts) > 1 else "")
            if not phone or not sms_url:
                raise RuntimeError(f"手机号接码行缺少 phone 或 sms_url: {line[:80]}")
            entries.append(PhoneSmsEntry(phone=phone, sms_url=sms_url))
        return entries

    def normalize_phone(self, phone: str) -> str:
        value = str(phone or "").strip()
        if value.startswith("+"):
            return value
        digits = "".join(ch for ch in value if ch.isdigit())
        if self.country_code and digits.startswith(self.country_code):
            return f"+{digits}"
        if self.country_code and digits:
            return f"+{self.country_code}{digits}"
        return value

    def prepare_entry(self, entry: PhoneSmsEntry) -> PhoneSmsEntry:
        phone = self.normalize_phone(entry.phone)
        existing = self._read_code(entry.sms_url, ignored_numbers={phone})
        if existing:
            self._ignored_codes_by_url.setdefault(entry.sms_url, set()).add(existing)
        return PhoneSmsEntry(phone=phone, sms_url=entry.sms_url)

    def _fetch_text(self, sms_url: str) -> str:
        response = self.session.get(sms_url, timeout=20, proxies=self.proxies)
        text = response.text or ""
        if response.status_code >= 400:
            raise RuntimeError(f"接码 URL 请求失败: status={response.status_code} body={text[:160]}")
        return text

    def _read_code(self, sms_url: str, *, ignored_numbers: set[str] | None = None) -> str:
        return _extract_sms_code(self._fetch_text(sms_url), ignored_numbers=ignored_numbers)

    def wait_for_code(self, entry: PhoneSmsEntry, *, timeout: int) -> str:
        deadline = time.time() + max(10, int(timeout or 120))
        last_text = ""
        ignored = set(self._ignored_codes_by_url.get(entry.sms_url, set()))
        ignored.add("".join(ch for ch in entry.phone if ch.isdigit()))
        while time.time() < deadline:
            try:
                text = self._fetch_text(entry.sms_url)
                last_text = text[:300]
                code = _extract_sms_code(text, ignored_numbers={entry.phone})
                if code and code not in ignored:
                    return code
            except Exception as exc:
                last_text = str(exc)
            time.sleep(self.poll_interval)
        raise RuntimeError(f"等待短信验证码超时({timeout}s): {last_text[:160]}")


@dataclass
class PhoneRegistrationJob:
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


class PhoneRegistrationManager:
    def __init__(self) -> None:
        self._jobs: dict[str, PhoneRegistrationJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(os.environ.get("UPISCAN_PHONE_REGISTER_WORKERS", "2") or 2)))
        self._max_logs = env_int("UPISCAN_REGISTER_JOB_MAX_LOGS", 500, maximum=5000)
        self._max_items = env_int("UPISCAN_REGISTER_JOB_MAX_ITEMS", 1000, maximum=10000)
        self._max_jobs = env_int("UPISCAN_REGISTER_JOB_MAX_HISTORY", 50, maximum=1000)
        self._job_ttl_seconds = env_int("UPISCAN_REGISTER_JOB_TTL_SECONDS", 6 * 60 * 60, minimum=60)
        self._concurrency_max = env_int("UPISCAN_PHONE_REGISTER_CONCURRENCY_MAX", 2, maximum=8)

    def create_job(self, payload: dict[str, Any]) -> PhoneRegistrationJob:
        job_id = uuid.uuid4().hex
        job = PhoneRegistrationJob(job_id=job_id)
        with self._lock:
            prune_jobs(self._jobs, max_jobs=self._max_jobs, ttl_seconds=self._job_ttl_seconds)
            self._jobs[job_id] = job
        self._executor.submit(self._run_job, job_id, payload)
        return job

    def get_job(self, job_id: str) -> PhoneRegistrationJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _log(self, job_id: str, message: str, level: str = "info") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.logs.append({"timestamp": utc_now(), "message": message, "level": level})
            trim_sequence(job.logs, self._max_logs)
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
            trim_sequence(job.items, self._max_items)
            job.updated_at = utc_now()

    def _registration_proxy_pool(self, job_id: str, payload: dict[str, Any], target_count: int = 1) -> list[str]:
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
            if normalized and normalized not in seen:
                seen.add(normalized)
                proxies.append(normalized)
        return filter_clean_proxies(
            proxies,
            payload,
            log=lambda message, level="info": self._log(job_id, message, level),
            target_count=max(1, int(target_count or 1)),
        )

    @staticmethod
    def _proxy_for_attempt(proxy_pool: list[str], index: int, attempt_no: int) -> str:
        if not proxy_pool:
            return ""
        return proxy_pool[((max(1, index) - 1) + (max(1, attempt_no) - 1)) % len(proxy_pool)]

    @staticmethod
    def _sms_provider_key(payload: dict[str, Any]) -> str:
        value = str(payload.get("sms_provider") or os.environ.get("SMS_PROVIDER") or "user_phone_url").strip().lower()
        if bool(payload.get("use_resource_pool")):
            return "resource_pool"
        aliases = {
            "": "user_phone_url",
            "user": "user_phone_url",
            "manual": "user_phone_url",
            "phone_url": "user_phone_url",
            "pool": "resource_pool",
            "phone_pool": "resource_pool",
            "resource": "resource_pool",
            "resource_pool": "resource_pool",
            "sms-activate": "sms_activate",
            "smsactivate": "sms_activate",
            "hero": "herosms",
            "hero_sms": "herosms",
            "sms_bower": "smsbower",
        }
        return aliases.get(value, value)

    def _build_targets(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        provider_key = self._sms_provider_key(payload)
        if provider_key == "resource_pool":
            count = max(1, min(100, int(payload.get("provider_count") or 1)))
            resource_provider = str(payload.get("resource_provider") or "user_phone_url").strip() or "user_phone_url"
            return [
                {
                    "mode": "resource_pool",
                    "provider": "user_phone_url",
                    "resource_provider": resource_provider,
                    "phone": f"{resource_provider}#{index + 1}",
                }
                for index in range(count)
            ]
        if provider_key == "user_phone_url":
            sms_proxy = str(payload.get("sms_proxy") or "").strip() or None
            pool = UserProvidedPhoneSmsPool(
                str(payload.get("phone_text") or ""),
                proxy=sms_proxy,
                country_code=str(payload.get("country_code") or ""),
                poll_interval=int(payload.get("sms_poll_interval") or 3),
            )
            targets = []
            for entry in pool.entries:
                prepared = pool.prepare_entry(entry)
                targets.append({"mode": "user_phone_url", "entry": prepared, "phone": prepared.phone})
            return targets
        count = max(1, min(100, int(payload.get("provider_count") or 1)))
        return [{"mode": "sms_provider", "provider": provider_key, "phone": f"{provider_key}#{index + 1}"} for index in range(count)]

    def _run_job(self, job_id: str, payload: dict[str, Any]) -> None:
        try:
            targets = self._build_targets(payload)
            if not targets:
                raise RuntimeError("没有可用的手机注册目标")
            proxy_pool = self._registration_proxy_pool(job_id, payload, len(targets))
            requested_concurrency = int(payload.get("concurrency") or 1)
            concurrency = max(1, min(self._concurrency_max, requested_concurrency, len(targets)))
            provider_key = self._sms_provider_key(payload)
            self._patch(job_id, status="running", total=len(targets))
            self._log(job_id, f"手机注册任务开始：短信来源 {provider_key}，目标 {len(targets)} 个，并发 {concurrency}")
            if requested_concurrency > concurrency:
                self._log(job_id, f"手机注册并发已限制：请求 {requested_concurrency}，实际 {concurrency}", "warn")
            if proxy_pool:
                self._log(job_id, f"注册 IP 池已加载：{len(proxy_pool)} 条，失败后切换下一条")
            else:
                self._log(job_id, "注册 IP 池为空：将使用直连", "warn")
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    executor.submit(self._register_one, job_id, target, index + 1, payload, proxy_pool)
                    for index, target in enumerate(targets)
                ]
                for future in as_completed(futures):
                    self._append_item(job_id, future.result())
            job = self.get_job(job_id)
            self._patch(job_id, status="completed" if job and job.failed == 0 else "failed")
            job = self.get_job(job_id)
            self._log(job_id, f"手机注册任务结束：成功 {job.success if job else 0}，失败 {job.failed if job else 0}")
        except Exception as exc:
            self._patch(job_id, status="failed", error=str(exc))
            self._log(job_id, str(exc), "error")

    def _register_one(self, job_id: str, target: dict[str, Any], index: int, payload: dict[str, Any], proxy_pool: list[str]) -> dict[str, Any]:
        password = str(payload.get("chatgpt_password") or "").strip() or generate_password()
        max_attempts = max(1, min(5, int(payload.get("registration_retry_attempts") or 1)))
        last_error = ""
        tried_labels: list[str] = []
        display_phone = str(target.get("phone") or f"phone#{index}")
        self._log(job_id, f"[{index}] 开始手机注册 {display_phone}")
        for attempt_no in range(1, max_attempts + 1):
            selected_proxy = self._proxy_for_attempt(proxy_pool, index, attempt_no)
            selected_label = proxy_label(selected_proxy)
            tried_labels.append(selected_label)
            self._log(job_id, f"[{index}] 第 {attempt_no}/{max_attempts} 次注册 {display_phone}，代理 {selected_label}")
            try:
                result = self._run_builtin_registration(target, password, payload, selected_proxy, task_id=f"{job_id}-{index}-{attempt_no}")
                token = str(result.get("access_token") or "").strip()
                if not token:
                    raise RuntimeError("手机注册执行器未返回 access_token")
                account_id, email, plan_type = _extract_identity(token)
                final_phone = str(result.get("phone_number") or display_phone)
                stored_result = dict(result)
                stored_result["registration_proxy_label"] = selected_label
                saved = account_library.upsert_account(
                    {
                        "account_key": account_id or email or final_phone,
                        "account_id": account_id,
                        "email": email,
                        "password": password,
                        "access_token": token,
                        "session_json": json.dumps(stored_result, ensure_ascii=False),
                        "plan_type": plan_type or str(result.get("plan_type") or "free"),
                        "status": "active",
                        "source": "phone_registration",
                        "channels": [],
                        "note": f"手机注册导入；手机号 {final_phone}；短信来源 {self._sms_provider_key(payload)}；注册代理 {selected_label}",
                    }
                )
                self._log(job_id, f"[{index}] 注册成功 {final_phone}，代理 {selected_label}")
                return {
                    "ok": True,
                    "phone": final_phone,
                    "email": email,
                    "account_id": account_id,
                    "account": saved,
                    "proxy_label": selected_label,
                    "attempts": attempt_no,
                }
            except Exception as exc:
                last_error = str(exc)
                self._log(job_id, f"[{index}] 第 {attempt_no}/{max_attempts} 次失败 {display_phone}，代理 {selected_label}: {exc}", "warn" if attempt_no < max_attempts else "error")
        return {
            "ok": False,
            "phone": display_phone,
            "error": last_error,
            "proxy_label": tried_labels[-1] if tried_labels else "direct",
            "attempts": len(tried_labels) or max_attempts,
            "tried_proxy_labels": tried_labels,
        }

    @staticmethod
    def _apply_sms_config(config: dict[str, Any], payload: dict[str, Any]) -> None:
        env_defaults = {
            "sms_provider": "SMS_PROVIDER",
            "sms_api_key": "SMS_API_KEY",
            "sms_service": "SMS_SERVICE",
            "sms_country": "SMS_COUNTRY",
            "herosms_api_key": "HEROSMS_API_KEY",
            "smsbower_api_key": "SMSBOWER_API_KEY",
            "sms_activate_api_key": "SMS_ACTIVATE_API_KEY",
        }
        for key, env_name in env_defaults.items():
            value = os.environ.get(env_name, "").strip()
            if value and not config.get(key):
                config[key] = value
        for key in (
            "sms_provider",
            "sms_proxy",
            "sms_api_key",
            "sms_service",
            "sms_country",
            "sms_activate_api_key",
            "sms_activate_country",
            "herosms_api_key",
            "herosms_service",
            "herosms_country",
            "herosms_max_price",
            "register_reuse_phone_to_max",
            "register_phone_success_max",
            "smsbower_api_key",
            "smsbower_service",
            "smsbower_country",
            "smsbower_max_price",
            "smsbower_min_price",
            "smsbower_provider_ids",
        ):
            if payload.get(key) not in (None, ""):
                config[key] = payload.get(key)

    def _run_builtin_registration(self, target: dict[str, Any], password: str, payload: dict[str, Any], proxy: str, *, task_id: str) -> dict[str, Any]:
        runtime_root = Path(__file__).resolve().parent / "registration_runtime"
        runtime_path = str(runtime_root)
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)
        try:
            from core.base_sms import create_sms_provider  # type: ignore
            from core.browser.session import BrowserSession  # type: ignore
            from platforms.chatgpt.phone_register import continue_after_sms, phone_registration_flow  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"手机注册运行时加载失败，请确认浏览器依赖已安装: {exc}") from exc

        config = dict(payload.get("config") or {})
        self._apply_sms_config(config, payload)
        config.update(
            {
                "headed": bool(payload.get("headed", False)),
                "browser_engine": str(payload.get("browser_engine") or "playwright"),
                "browser_profile_dir": str(Path.cwd() / "data" / "browser_profiles" / "phone_register" / task_id),
                "_force_fresh_browser_context": True,
                "_log_fn": lambda message: self._log(task_id.split("-", 1)[0], str(message)),
            }
        )
        if proxy:
            config["proxy"] = proxy

        country_code = str(payload.get("country_code") or "1").strip() or "1"
        country_name = str(payload.get("country_name") or "United States").strip() or "United States"
        sms_timeout = int(payload.get("sms_timeout") or 180)
        config["country_code"] = country_code
        config["country_name"] = country_name
        provider = None
        activation_id = ""

        if target.get("mode") == "sms_provider":
            provider_key = str(target.get("provider") or self._sms_provider_key(payload))
            if provider_key == "resource_pool":
                provider_key = "user_phone_url"
            generic_api_key = str(payload.get("sms_api_key") or config.get("sms_api_key") or os.environ.get("SMS_API_KEY") or "").strip()
            if generic_api_key and provider_key in {"herosms", "herosms_api"}:
                config["herosms_api_key"] = generic_api_key
            if generic_api_key and provider_key in {"sms_activate", "sms_activate_api"}:
                config["sms_activate_api_key"] = generic_api_key
            if generic_api_key and provider_key in {"smsbower", "smsbower_api"}:
                config["smsbower_api_key"] = generic_api_key
            config.setdefault("sms_service", str(payload.get("sms_service") or "dr"))
            config.setdefault("sms_country", str(payload.get("sms_country") or payload.get("sms_activate_country") or ""))
            provider = create_sms_provider(provider_key, config)
            service = str(config.get("sms_service") or "dr")
            country = str(config.get("sms_country") or "")
            self._log(task_id.split("-", 1)[0], f"从 {provider_key} 租用手机号：service={service} country={country or 'default'}")
            activation = provider.get_number(service=service, country=country)
            activation_id = activation.activation_id
            phone = activation.phone_number
        elif target.get("mode") == "resource_pool":
            resource_provider = str(target.get("resource_provider") or payload.get("resource_provider") or "user_phone_url").strip() or "user_phone_url"
            config["_resource_provider"] = resource_provider
            config["dashboard_task_id"] = task_id
            config["sms_provider"] = "user_phone_url"
            config.setdefault("sms_service", str(payload.get("sms_service") or "dr"))
            provider = create_sms_provider("user_phone_url", config)
            self._log(task_id.split("-", 1)[0], f"从手机号资源池租用号码：provider={resource_provider}")
            activation = provider.get_number(service=str(config.get("sms_service") or "dr"), country=str(payload.get("sms_country") or ""))
            activation_id = activation.activation_id
            phone = activation.phone_number
        else:
            entry = target["entry"]
            phone = entry.phone
            activation_id = entry.sms_url
            sms_pool = UserProvidedPhoneSmsPool(
                f"{entry.phone}|{entry.sms_url}",
                proxy=str(payload.get("sms_proxy") or "") or None,
                country_code=country_code,
                poll_interval=int(payload.get("sms_poll_interval") or 3),
            )
            prepared = sms_pool.prepare_entry(entry)
            phone = prepared.phone

        with BrowserSession(config) as session:
            try:
                first = phone_registration_flow(
                    session.page,
                    phone,
                    password,
                    country_code=country_code,
                    country_name=country_name,
                    headed=bool(payload.get("headed", False)),
                    log=lambda message: self._log(task_id.split("-", 1)[0], str(message)),
                )
                if getattr(first, "success", False) and getattr(first, "access_token", ""):
                    token = str(first.access_token)
                else:
                    error = str(getattr(first, "error", "") or "")
                    if error and error != "AWAITING_SMS_CODE":
                        if provider and hasattr(provider, "mark_send_failed"):
                            provider.mark_send_failed(activation_id, reason=error)
                        raise RuntimeError(error)
                    if provider and hasattr(provider, "mark_send_succeeded"):
                        provider.mark_send_succeeded(activation_id)
                    self._log(task_id.split("-", 1)[0], f"等待短信验证码 {phone}")
                    if provider:
                        code = provider.get_code(activation_id, timeout=sms_timeout)
                        if not code:
                            raise RuntimeError("短信服务商未返回验证码")
                    else:
                        code = sms_pool.wait_for_code(prepared, timeout=sms_timeout)
                    final = continue_after_sms(session.page, code, log=lambda message: self._log(task_id.split("-", 1)[0], str(message)))
                    if not getattr(final, "success", False):
                        reason = str(getattr(final, "failure_reason", "") or getattr(final, "error", "") or "手机注册失败")
                        if provider and hasattr(provider, "mark_code_failed"):
                            provider.mark_code_failed(activation_id, reason=reason)
                        raise RuntimeError(reason)
                    token = str(getattr(final, "access_token", "") or "")
                    if provider and hasattr(provider, "report_success"):
                        provider.report_success(activation_id)
            except Exception:
                if provider and activation_id:
                    try:
                        provider.cancel(activation_id)
                    except Exception:
                        pass
                raise

            if not token:
                raise RuntimeError("手机注册成功后未提取到 access_token")
            account_id, email, plan_type = _extract_identity(token)
            storage_path = Path.cwd() / "data" / "registered_accounts" / f"phone_storage_{task_id}.json"
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_state = session.save_storage_state(str(storage_path))

        return {
            "success": True,
            "status": "phone_registered",
            "stage": "registered",
            "registration_mode": "phone",
            "registration_status": "registered",
            "task_id": task_id,
            "phone_number": phone,
            "sms_provider": self._sms_provider_key(payload),
            "sms_activation_id": activation_id,
            "email": email,
            "account_id": account_id,
            "password": password,
            "generated_chatgpt_password": password,
            "plan_type": plan_type or "free",
            "access_token": token,
            "chatgpt_access_token_initial": token,
            "browser_engine": str(config.get("browser_engine") or ""),
            "browser_profile_dir": str(config.get("browser_profile_dir") or ""),
            "browser_storage_state_path": storage_state,
        }


phone_registration_manager = PhoneRegistrationManager()
