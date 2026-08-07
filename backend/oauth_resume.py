from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import account_library, resource_pool
from .email_registration import LinkApiMailbox
from .extractor.proxy import normalize_proxy_url, proxy_label


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OAuthResumeJob:
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


class OAuthResumeManager:
    def __init__(self) -> None:
        self._jobs: dict[str, OAuthResumeJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(os.environ.get("UPISCAN_OAUTH_RESUME_WORKERS", "2") or 2)))

    def create_job(self, payload: dict[str, Any]) -> OAuthResumeJob:
        job_id = uuid.uuid4().hex
        job = OAuthResumeJob(job_id=job_id)
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run_job, job_id, payload)
        return job

    def get_job(self, job_id: str) -> OAuthResumeJob | None:
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
            tasks = self._build_tasks(job_id, payload)
            if not tasks:
                raise RuntimeError("没有可续跑的账号或 resume JSON")
            proxy_pool = self._registration_proxy_pool(payload, len(tasks))
            concurrency = max(1, min(6, int(payload.get("concurrency") or 1), len(tasks)))
            self._patch(job_id, status="running", total=len(tasks))
            self._log(job_id, f"OAuth 绑定/续跑任务开始：{len(tasks)} 个账号，并发 {concurrency}")
            if proxy_pool:
                self._log(job_id, f"OAuth IP 池已加载：{len(proxy_pool)} 条，失败后按顺序切换")
            else:
                self._log(job_id, "OAuth IP 池为空：将使用 resume 内代理或直连", "warn")

            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(self._run_one, job_id, task, index + 1, payload, proxy_pool)
                    for index, task in enumerate(tasks)
                ]
                for future in as_completed(futures):
                    self._append_item(job_id, future.result())

            job = self.get_job(job_id)
            self._patch(job_id, status="completed" if job and job.failed == 0 else "failed")
            job = self.get_job(job_id)
            self._log(job_id, f"OAuth 绑定/续跑结束：成功 {job.success if job else 0}，失败 {job.failed if job else 0}")
        except Exception as exc:
            self._patch(job_id, status="failed", error=str(exc))
            self._log(job_id, str(exc), "error")

    def _build_tasks(self, job_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        account_ids = payload.get("account_ids")
        if not isinstance(account_ids, list):
            account_ids = []
        legacy_id = int(payload.get("account_id") or 0)
        if legacy_id > 0 and legacy_id not in account_ids:
            account_ids.append(legacy_id)

        for account_id in account_ids:
            detail = account_library.get_account(int(account_id))
            if not detail:
                self._log(job_id, f"账号库 ID {account_id} 不存在，已跳过", "warn")
                continue
            contract = self._contract_from_account(detail, payload)
            tasks.append({"source": "account", "account": detail, "contract": contract})

        for index, record in enumerate(self._parse_resume_json(payload.get("resume_json")), start=1):
            tasks.append({"source": "resume_json", "account": None, "contract": self._normalize_resume_record(record, payload), "resume_index": index})

        return tasks

    @staticmethod
    def _parse_resume_json(value: Any) -> list[dict[str, Any]]:
        text = str(value or "").strip()
        if not text:
            return []
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except ValueError:
            records: list[dict[str, Any]] = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError as exc:
                    raise RuntimeError(f"resume JSON 行格式错误: {line[:80]}") from exc
                if isinstance(item, dict):
                    records.append(item)
            return records
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            items = parsed.get("items") or parsed.get("accounts")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            return [parsed]
        return []

    @staticmethod
    def _session_json(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        if not text.startswith("{"):
            return {}
        try:
            parsed = json.loads(text)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _contract_from_account(self, account: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        session = self._session_json(str(account.get("session_json") or ""))
        merged = dict(session)
        merged.setdefault("email", account.get("email") or "")
        merged.setdefault("account_id", account.get("account_id") or "")
        merged.setdefault("password", account.get("password") or "")
        merged.setdefault("generated_chatgpt_password", account.get("password") or "")
        if payload.get("chatgpt_password"):
            merged["password"] = str(payload.get("chatgpt_password") or "").strip()
            merged["generated_chatgpt_password"] = merged["password"]
        return self._normalize_resume_record(merged, payload)

    @staticmethod
    def _normalize_resume_record(record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        merged = dict(record or {})
        password = str(payload.get("chatgpt_password") or merged.get("password") or merged.get("generated_chatgpt_password") or "").strip()
        if password:
            merged["password"] = password
            merged["generated_chatgpt_password"] = password
        if not str(merged.get("email") or merged.get("phone_number") or "").strip():
            raise RuntimeError("resume JSON 缺少 email 或 phone_number")
        if not str(merged.get("browser_storage_state_path") or "").strip():
            oauth_state = str(merged.get("oauth_browser_storage_state_path") or "").strip()
            if oauth_state:
                merged["browser_storage_state_path"] = oauth_state
        if not str(merged.get("browser_storage_state_path") or "").strip():
            raise RuntimeError("resume JSON 缺少 browser_storage_state_path")
        if not password:
            raise RuntimeError("resume JSON 缺少 password/generated_chatgpt_password")
        return merged

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
            if normalized and normalized not in seen:
                seen.add(normalized)
                proxies.append(normalized)
        return proxies

    @staticmethod
    def _proxy_for_attempt(proxy_pool: list[str], index: int, attempt_no: int, contract: dict[str, Any]) -> str:
        if proxy_pool:
            return proxy_pool[((max(1, index) - 1) + (max(1, attempt_no) - 1)) % len(proxy_pool)]
        return str(contract.get("registration_proxy") or "").strip()

    def _run_one(self, job_id: str, task: dict[str, Any], index: int, payload: dict[str, Any], proxy_pool: list[str]) -> dict[str, Any]:
        contract = dict(task["contract"])
        account = task.get("account")
        identity = str(payload.get("login_identity") or contract.get("email") or contract.get("phone_number") or "").strip()
        display = str(contract.get("email") or contract.get("phone_number") or identity or f"resume-{index}")
        max_attempts = max(1, min(5, int(payload.get("registration_retry_attempts") or 1)))
        last_error = ""
        tried_labels: list[str] = []
        self._log(job_id, f"[{index}] 开始 OAuth 续跑：{display}")
        for attempt_no in range(1, max_attempts + 1):
            selected_proxy = self._proxy_for_attempt(proxy_pool, index, attempt_no, contract)
            selected_label = proxy_label(selected_proxy)
            tried_labels.append(selected_label)
            self._log(job_id, f"[{index}] 第 {attempt_no}/{max_attempts} 次 OAuth 续跑，代理 {selected_label}")
            try:
                result = self._run_runtime(job_id, index, attempt_no, contract, payload, selected_proxy)
                saved = self._persist_result(account, contract, result)
                self._log(job_id, f"[{index}] OAuth 续跑成功：{result.get('email') or display}，代理 {selected_label}")
                return {
                    "ok": True,
                    "email": str(result.get("email") or contract.get("email") or ""),
                    "account_id": str(result.get("account_id") or contract.get("account_id") or ""),
                    "account": saved,
                    "proxy_label": selected_label,
                    "attempts": attempt_no,
                    "result": self._public_result(result),
                }
            except Exception as exc:
                last_error = str(exc)
                self._log(job_id, f"[{index}] 第 {attempt_no}/{max_attempts} 次失败，代理 {selected_label}: {exc}", "warn" if attempt_no < max_attempts else "error")
        return {
            "ok": False,
            "email": str(contract.get("email") or ""),
            "account_id": str(contract.get("account_id") or ""),
            "error": last_error,
            "proxy_label": tried_labels[-1] if tried_labels else "direct",
            "attempts": len(tried_labels) or max_attempts,
            "tried_proxy_labels": tried_labels,
        }

    def _run_runtime(self, job_id: str, index: int, attempt_no: int, contract: dict[str, Any], payload: dict[str, Any], proxy: str) -> dict[str, Any]:
        runtime_root = Path(__file__).resolve().parent / "registration_runtime"
        runtime_path = str(runtime_root)
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)
        try:
            from core.browser.session import BrowserSession  # type: ignore
            from registration.phone_bind import create_binding_phone_callback  # type: ignore
            from registration.patch_resume_bind import run_patch_resume_bind  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"OAuth 续跑运行时加载失败，请确认浏览器依赖已安装: {exc}") from exc

        task_id = f"{job_id}-{index}-{attempt_no}"
        resume_file = self._write_resume_file(task_id, contract)
        config = dict(payload.get("config") or {})
        config.update(
            {
                "headed": bool(payload.get("headed", False)),
                "browser_engine": str(payload.get("browser_engine") or "playwright"),
                "dashboard_task_id": task_id,
                "_browser_storage_state": str(contract.get("browser_storage_state_path") or ""),
                "browser_profile_dir": str(Path.cwd() / "data" / "browser_profiles" / "oauth_resume" / task_id),
                "browser_storage_state_path": str(Path.cwd() / "data" / "registered_accounts" / f"oauth_storage_{task_id}.json"),
                "_log_fn": lambda message: self._log(job_id, str(message)),
            }
        )
        self._apply_bind_phone_config(config, payload)
        if proxy:
            config["proxy"] = proxy
            contract["registration_proxy"] = proxy

        bind_email, otp_callback = self._mail_otp_callback(job_id, index, payload, contract, proxy)
        phone_callback, phone_cleanup = create_binding_phone_callback(config, log_fn=lambda message: self._log(job_id, str(message)))
        with BrowserSession(config) as session:
            try:
                result = run_patch_resume_bind(
                    session,
                    config=config,
                    resume_file=resume_file,
                    log_fn=lambda message: self._log(job_id, str(message)),
                    login_identity=str(payload.get("login_identity") or ""),
                    password=str(payload.get("chatgpt_password") or ""),
                    bind_email=bind_email,
                    otp_callback=otp_callback,
                    phone_callback=phone_callback,
                    proxy=proxy or None,
                    redirect_uri=str(payload.get("redirect_uri") or ""),
                    client_id=str(payload.get("client_id") or ""),
                    authorize_url=str(payload.get("authorize_url") or ""),
                    allow_page_fallback=bool(payload.get("allow_page_fallback", True)),
                )
                saved_state = session.save_storage_state(str(config["browser_storage_state_path"]))
                if saved_state:
                    result["oauth_browser_storage_state_path"] = saved_state
            finally:
                phone_cleanup()

        if not isinstance(result, dict):
            raise RuntimeError("OAuth 续跑未返回有效结果")
        token = str(result.get("access_token") or "").strip()
        refresh = str(result.get("refresh_token") or "").strip()
        if not token and not refresh:
            raise RuntimeError(f"OAuth 续跑结果缺少 token: {result}")
        result.setdefault("resume_file", str(resume_file))
        result.setdefault("email", contract.get("email") or "")
        result.setdefault("account_id", contract.get("account_id") or "")
        result.setdefault("oauth_resume_at", utc_now())
        return result

    @staticmethod
    def _apply_bind_phone_config(config: dict[str, Any], payload: dict[str, Any]) -> None:
        env_defaults = {
            "bind_sms_provider": "BIND_SMS_PROVIDER",
            "bind_sms_api_key": "BIND_SMS_API_KEY",
            "bind_sms_service": "BIND_SMS_SERVICE",
            "bind_sms_country": "BIND_SMS_COUNTRY",
            "bind_sms_proxy": "BIND_SMS_PROXY",
        }
        for key, env_name in env_defaults.items():
            value = os.environ.get(env_name, "").strip()
            if value and not config.get(key):
                config[key] = value
        for key in (
            "bind_sms_provider",
            "bind_use_resource_pool",
            "bind_resource_provider",
            "bind_sms_phone_url",
            "bind_sms_phone_urls",
            "bind_sms_phone_url_file",
            "bind_sms_proxy",
            "bind_sms_api_key",
            "bind_sms_service",
            "bind_sms_country",
            "bind_country_code",
            "bind_country_name",
            "bind_herosms_api_key",
            "bind_herosms_service",
            "bind_herosms_country",
            "bind_herosms_max_price",
            "bind_smsbower_api_key",
            "bind_smsbower_service",
            "bind_smsbower_country",
            "bind_smsbower_max_price",
            "bind_smsbower_min_price",
            "bind_smsbower_provider_ids",
            "bind_sms_activate_api_key",
            "bind_sms_activate_country",
        ):
            if payload.get(key) not in (None, ""):
                config[key] = payload.get(key)

    @staticmethod
    def _write_resume_file(task_id: str, contract: dict[str, Any]) -> str:
        path = Path.cwd() / "data" / "resume_jobs" / f"{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _mail_otp_callback(self, job_id: str, index: int, payload: dict[str, Any], contract: dict[str, Any], proxy: str) -> tuple[str, Any | None]:
        mailbox_text = str(payload.get("bind_email_text") or "").strip()
        bind_email = str(payload.get("bind_email") or "").strip()
        resource_key = ""
        provider = str(payload.get("bind_email_resource_provider") or "icloud_api").strip() or "icloud_api"
        outlook_row: dict[str, str] | None = None
        provider_row: dict[str, str] | None = None
        if bool(payload.get("bind_email_use_resource_pool")):
            leased = resource_pool.lease_email_mailboxes(provider=provider, count=1, lease_id=job_id)
            if leased:
                row = leased[0]
                provider_row = row
                if provider == "outlook_token":
                    outlook_row = row
                resource_key = str(row.get("_resource_key") or "").strip()
                bind_email = bind_email or str(row.get("email") or "")
                parts = [str(row.get("email") or "")]
                if row.get("inbox_url"):
                    parts.append(str(row["inbox_url"]))
                if row.get("code_url"):
                    parts.append(f"code:{row['code_url']}")
                if row.get("mail_url"):
                    parts.append(f"mail:{row['mail_url']}")
                mailbox_text = "----".join(parts)
                self._log(job_id, f"[{index}] 邮箱 OTP 已从资源池租用：{bind_email}，provider={provider}")
            elif not mailbox_text:
                self._log(job_id, f"[{index}] 邮箱资源池没有可租用资源，跳过邮箱 OTP：provider={provider}", "warn")
        if provider == "outlook_token" and outlook_row:
            try:
                from core.mailbox.outlook_token import OutlookTokenMailbox  # type: ignore
            except Exception as exc:
                raise RuntimeError(f"Outlook token mailbox runtime failed to load: {exc}") from exc

            config = dict(payload.get("config") or {})
            if proxy:
                config["proxy"] = proxy
            config.update(
                {
                    "outlook_email": str(outlook_row.get("email") or ""),
                    "outlook_password": str(outlook_row.get("password") or ""),
                    "outlook_client_id": str(outlook_row.get("client_id") or ""),
                    "outlook_refresh_token": str(outlook_row.get("refresh_token") or ""),
                    "email_otp_timeout": int(payload.get("email_otp_timeout") or 200),
                    "email_otp_poll_interval": int(payload.get("email_otp_poll_interval") or 3),
                }
            )
            mailbox = OutlookTokenMailbox(config, log_fn=lambda message: self._log(job_id, str(message)))
            account = mailbox.first(str(outlook_row.get("email") or ""), include_used=True)
            bind_email = bind_email or account.email
            rejected_codes: set[str] = set()

            def otp_callback() -> str:
                try:
                    code = mailbox.wait_for_openai_code(
                        account,
                        timeout=int(payload.get("email_otp_timeout") or 200),
                        not_before=datetime.now(timezone.utc) - timedelta(seconds=30),
                        reject_codes=rejected_codes,
                    )
                    if code:
                        rejected_codes.add(code)
                        if resource_key:
                            resource_pool.report_resource(job_id, resource_key, success=True)
                    return code
                except Exception as exc:
                    if resource_key:
                        resource_pool.report_resource(
                            job_id,
                            resource_key,
                            success=False,
                            cooldown_until=resource_pool.cooldown_until(60 * 60),
                            error=str(exc)[:500],
                        )
                    raise

            return bind_email, otp_callback

        if provider in {"icloud_privacy", "forwarded_domain", "cfworker_admin_api"}:
            try:
                from core.mailbox.forwarded_domain import ForwardedDomainMailbox  # type: ignore
                from core.mailbox.providers import CFWorkerMailbox, ICloudPrivacyMailbox  # type: ignore
            except Exception as exc:
                raise RuntimeError(f"Mailbox provider runtime failed to load: {exc}") from exc

            if provider_row is None and mailbox_text:
                if provider == "icloud_privacy":
                    key, data = resource_pool.parse_icloud_privacy_entries(mailbox_text)[0]
                    provider_row = {
                        "email": str(data.get("email") or key),
                        "imap_user": str(data.get("imap_user") or ""),
                        "imap_pass": str(data.get("imap_pass") or ""),
                        "imap_host": str(data.get("imap_host") or ""),
                        "imap_port": str(data.get("imap_port") or ""),
                    }
                elif provider == "forwarded_domain":
                    key, data = resource_pool.parse_forwarded_domain_entries(mailbox_text)[0]
                    provider_row = {
                        "email": f"*@{key}",
                        "domain": str(data.get("domain") or key),
                        "imap_user": str(data.get("imap_user") or ""),
                        "imap_pass": str(data.get("imap_pass") or ""),
                        "imap_host": str(data.get("imap_host") or ""),
                        "imap_port": str(data.get("imap_port") or ""),
                    }
                else:
                    key, data = resource_pool.parse_cfworker_entries(mailbox_text)[0]
                    provider_row = {
                        "email": f"*@{data.get('domain') or key}",
                        "api_url": str(data.get("api_url") or ""),
                        "admin_token": str(data.get("admin_token") or ""),
                        "domain": str(data.get("domain") or ""),
                        "fingerprint": str(data.get("fingerprint") or ""),
                    }

            if not provider_row:
                return bind_email, None

            config = dict(payload.get("config") or {})
            if proxy:
                config["proxy"] = proxy
            config.update(
                {
                    "mailbox_domain": str(provider_row.get("domain") or ""),
                    "mailbox_imap_user": str(provider_row.get("imap_user") or ""),
                    "mailbox_imap_pass": str(provider_row.get("imap_pass") or ""),
                    "mailbox_imap_host": str(provider_row.get("imap_host") or ""),
                    "mailbox_imap_port": str(provider_row.get("imap_port") or ""),
                    "icloud_privacy_order_text": str(provider_row.get("email") or ""),
                    "icloud_privacy_email": str(provider_row.get("email") or ""),
                    "cfworker_api_url": str(provider_row.get("api_url") or ""),
                    "cfworker_admin_token": str(provider_row.get("admin_token") or ""),
                    "cfworker_domain": str(provider_row.get("domain") or ""),
                    "cfworker_fingerprint": str(provider_row.get("fingerprint") or ""),
                }
            )
            if provider == "icloud_privacy":
                mailbox = ICloudPrivacyMailbox.from_config(config)
                account = mailbox.account_for_email(str(provider_row.get("email") or ""))
            elif provider == "forwarded_domain":
                mailbox = ForwardedDomainMailbox.from_config(config)
                target_bind_email = bind_email if bind_email and not bind_email.startswith("*@") else ""
                account = mailbox.account_for_email(target_bind_email) if target_bind_email else mailbox.create_account()
            else:
                mailbox = CFWorkerMailbox.from_config(config)
                account = mailbox.create_account()
            bind_email = account.email
            before_ids: set[str] = set()
            try:
                before_ids = set(mailbox.get_current_ids(account) or set())
                self._log(job_id, f"[{index}] Mailbox OTP baseline loaded: provider={provider}, messages={len(before_ids)}")
            except Exception as exc:
                self._log(job_id, f"[{index}] Mailbox OTP baseline failed, continuing: {str(exc)[:160]}", "warn")

            def otp_callback() -> str:
                try:
                    code = mailbox.wait_for_code(account, timeout=int(payload.get("email_otp_timeout") or 200), before_ids=before_ids)
                    if code:
                        try:
                            before_ids.update(mailbox.get_current_ids(account) or set())
                        except Exception:
                            pass
                        if resource_key:
                            resource_pool.report_resource(job_id, resource_key, success=True)
                    return code
                except Exception as exc:
                    if resource_key:
                        resource_pool.report_resource(
                            job_id,
                            resource_key,
                            success=False,
                            cooldown_until=resource_pool.cooldown_until(60 * 60),
                            error=str(exc)[:500],
                        )
                    raise

            return bind_email, otp_callback

        if not mailbox_text:
            return bind_email, None

        mailbox = LinkApiMailbox(
            mailbox_text,
            proxy=str(payload.get("mailbox_proxy") or "") or proxy or None,
            poll_interval=int(payload.get("email_otp_poll_interval") or 3),
        )
        rows = mailbox.rows()
        if not rows:
            return bind_email, None
        identity = str(contract.get("email") or "").strip().lower()
        row = next((item for item in rows if str(item.get("email") or "").strip().lower() == identity), rows[(index - 1) % len(rows)])
        account = mailbox.account_from_row(row)
        bind_email = bind_email or account.email
        before_ids: set[str] = set()
        try:
            before_ids = set(mailbox.get_current_ids(account) or set())
            self._log(job_id, f"[{index}] 邮箱 OTP 基线：{account.email} 历史邮件 {len(before_ids)} 封")
        except Exception as exc:
            self._log(job_id, f"[{index}] 邮箱 OTP 基线获取失败，继续等待新验证码: {str(exc)[:160]}", "warn")

        def otp_callback() -> str:
            try:
                code = mailbox.wait_for_code(
                    account,
                    timeout=int(payload.get("email_otp_timeout") or 200),
                    before_ids=before_ids,
                )
                if code:
                    try:
                        before_ids.update(mailbox.get_current_ids(account) or set())
                    except Exception:
                        pass
                    if resource_key:
                        resource_pool.report_resource(job_id, resource_key, success=True)
                return code
            except Exception as exc:
                if resource_key:
                    resource_pool.report_resource(
                        job_id,
                        resource_key,
                        success=False,
                        cooldown_until=resource_pool.cooldown_until(60 * 60),
                        error=str(exc)[:500],
                    )
                raise

        return bind_email, otp_callback

    def _persist_result(self, account: dict[str, Any] | None, contract: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        existing_access_token = str((account or {}).get("access_token") or "").strip()
        access_token = str(result.get("access_token") or existing_access_token or "").strip()
        account_id = str(result.get("account_id") or contract.get("account_id") or "").strip()
        email = str(result.get("email") or contract.get("email") or "").strip()
        plan_type = str(contract.get("plan_type") or result.get("plan_type") or "").strip()
        existing_session = self._session_json(str((account or {}).get("session_json") or ""))
        merged_session = dict(existing_session)
        merged_session["oauth_resume"] = result
        merged_session["oauth_tokens"] = {
            key: result.get(key)
            for key in ("access_token", "refresh_token", "id_token", "account_id", "email", "expired", "last_refresh", "type")
            if result.get(key)
        }
        if result.get("oauth_browser_storage_state_path"):
            merged_session["oauth_browser_storage_state_path"] = result["oauth_browser_storage_state_path"]
        note_prefix = str((account or {}).get("note") or "").strip()
        note = "; ".join(item for item in [note_prefix, f"OAuth resume 绑定完成 {utc_now()}"] if item)

        if account and int(account.get("id") or 0) > 0:
            updated = account_library.update_account(
                int(account["id"]),
                {
                    "access_token": access_token,
                    "password": str((account or {}).get("password") or contract.get("password") or ""),
                    "session_json": json.dumps(merged_session, ensure_ascii=False),
                    "status": "active",
                    "note": note,
                },
            )
            detail = account_library.get_account(int(account["id"]))
            return account_library.strip_account_secrets(detail) if detail else (updated or {})

        return account_library.upsert_account(
            {
                "account_key": account_id or email or uuid.uuid4().hex,
                "account_id": account_id,
                "email": email,
                "password": str(contract.get("password") or ""),
                "access_token": access_token,
                "session_json": json.dumps(merged_session, ensure_ascii=False),
                "plan_type": plan_type,
                "status": "active",
                "source": "oauth_resume",
                "channels": [],
                "note": "OAuth resume 绑定导入",
            }
        )

    @staticmethod
    def _public_result(result: dict[str, Any]) -> dict[str, Any]:
        public = dict(result)
        for key in ("access_token", "refresh_token", "id_token"):
            value = str(public.get(key) or "")
            if value:
                public[f"{key}_preview"] = value[:12] + "..." + value[-8:] if len(value) > 24 else value
            public.pop(key, None)
        return public


oauth_resume_manager = OAuthResumeManager()
