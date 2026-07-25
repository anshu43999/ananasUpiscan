"""ExtractionContext — per-job state container that replaces module-level globals.

Each extraction task (process/thread) owns one ExtractionContext instance.
All shared mutable state is encapsulated here so that concurrent jobs do not
interfere with each other.  The original upi_extract.py used ~15 module-level
globals; this class collects them into a single, injectable context object.

Design rules:
- Construction: pass a config dict (keys mirror UPI_* env vars).
  When a key is missing the default from upi_extract.py is used.
- Runtime mutable state (proxy state, log file, counters, redaction set,
  thread-local log context) lives on the instance — never as module globals.
- Thread-safety: an RLock guards each mutable collection (exactly like the
  original module-level locks).
- Serialisation: to_config_dict() returns only the *configuration* portion
  (no runtime state) so the dictionary can be pickled and sent to a worker
  process.  The worker reconstructs a fresh ExtractionContext from that dict.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import time
from pathlib import Path
from threading import RLock, local
from typing import Any
from urllib.parse import unquote, urlsplit

from .proxy import (
    is_direct_remove_proxy_error,
    is_proxy_health_failure,
    is_upi_unavailable_error,
    normalize_country,
    normalize_pre_proxy_url,
    normalize_proxy_url,
    make_proxy_chain_seed,
    proxy_chain_key,
    proxy_key,
    proxy_label,
    proxy_pair_key,
    proxy_short,
    proxy_state_key,
)


# ── defaults matching upi_extract.py ──────────────────────────────────────────
_DEFAULT_CONFIG: dict[str, Any] = {
    # paths
    "script_dir": "",          # will be derived if empty
    "log_dir": "",             # defaults to {script_dir}/logs
    "dump_dir": "",            # defaults to {script_dir}/dumps
    "proxy_seed_file": "",     # defaults to {script_dir}/proxy_seeds.txt
    "proxy_state_file": "",    # defaults to {script_dir}/proxy_state.json
    "proxy_seeds": [],
    "proxy_seed_chains": [],

    # proxy defaults
    "default_proxy_scheme": "http",
    "pre_proxy": "",           # UPI_PRE_PROXY / PP_PRE_PROXY / PP_LOCAL_PROXY

    # country configuration
    "bootstrap_country": "JP",
    "promotion_countries": ["IN"],
    "provider_country": "IN",
    "provider_country_label": "IN",

    # retry / limits
    "max_retry": 1,
    "provider_per_checkout": 1,
    "max_approve_blocked": 5,
    "workers": 1,
    "workers_max": 1,
    "approve_retry_max": 10,
    "approve_sticky": True,
    "follow_redirect": True,
    "require_zero": True,
    "checkout_retry": 1,
    "upi_retry": 1,
    "provider_retry": 1,
    "ideal_max_minor_amount": 50,

    # dump / debug
    "dump": False,
    "dump_limit": 6000,

    # proxy scoring
    "proxy_score": True,
    "proxy_skip_failed": True,
    "proxy_remove_failed": True,
    "proxy_fail_cooldown": 180,
    "proxy_fail_skip_after": 1,
    "proxy_remove_after_fails": 3,

    # zero cache
    "zero_cache": True,
    "zero_cache_scheduling": False,
    "zero_cache_skip_bad": True,
    "zero_cache_ttl": 86400,

    # checkout modes
    "confirm_inline_pm": False,
    "update_tax_region": True,
    "use_promotion_stage": False,
    "promo_mode": "campaign",
    "trial_days": 30,

    # runtime/env aliases
    "PP_TOKEN": "",
    "UPI_TOKEN": "",
    "PP_SESSION_TOKEN": "",
    "PP_PROMO_MODE": "",
    "PP_PROMO_ID": "",
    "PP_ENTRY_POINT": "",
    "PP_TRIAL_DAYS": "",
    "PP_EXPECTED_AMOUNT": "",
    "UPI_COUPON_FALLBACK_PROMO_CAMPAIGN": "",
    "UPI_DUMP_WARMUP": "",
    "UPI_APPROVE_WARMUP": "",
    "UPI_APPROVE_PARALLEL": "",
    "UPI_APPROVE_RETRY_MAX": "",
    "UPI_APPROVE_STICKY": "",
    "UPI_UPDATE_CUSTOMER_DATA": "",
    "UPI_CHECKOUT_SNAPSHOT": "",
    "UPI_USE_FIXED_BILLING": "",
    "UPI_USE_PROMOTION_STAGE": "",
    "UPI_FLOW_MODE": "",
    "UPI_CHECKOUT_COUNTRY": "",
    "UPI_CHECKOUT_PROXY_COUNTRY": "",
    "IDEAL_MAX_MINOR_AMOUNT": "",
    "IDEAL_CHECKOUT_COUNTRY": "",
    "IDEAL_CHECKOUT_PROXY_COUNTRY": "",
    "IDEAL_PROVIDER_PROXY_COUNTRY": "",
    "IDEAL_BROWSER_LOCALE": "",
    "IDEAL_ELEMENTS_LOCALE": "",
    "IDEAL_BROWSER_TIMEZONE": "",
    "IDEAL_CHECKOUT_RETRY_MAX": "",
    "IDEAL_MAX_RETRY": "",
    "IDEAL_WORKERS": "",
    "IDEAL_WORKERS_MAX": "",
    "IDEAL_UPDATE_CUSTOMER_DATA": "",
    "IDEAL_UPDATE_TAX_REGION": "",
    "IDEAL_USE_FIXED_BILLING": "",
    "IDEAL_EMAIL": "",
    "IDEAL_NAME": "",
    "IDEAL_LINE1": "",
    "IDEAL_LINE2": "",
    "IDEAL_CITY": "",
    "IDEAL_POSTAL_CODE": "",
    "IDEAL_STATE": "",
    "IDEAL_BILLING_COUNTRY": "",

    # Stripe / ChatGPT constants
    "stripe_pk": "",
    "stripe_runtime_version": "",
    "chatgpt_client_version": "",
    "chatgpt_client_build_number": "",
    "stripe_version_full": "",

    # locale
    "browser_locale": "en-IN",
    "elements_locale": "en",
    "browser_timezone": "Asia/Kolkata",
    "saved_payment_value": "never",

    # billing defaults
    "billing_country": "IN",
    "billing_email": "redacted@example.invalid",
    "billing_name": "Aisha Sharma",
    "billing_line1": "24 Park Street",
    "billing_line2": "",
    "billing_city": "Kolkata",
    "billing_postal_code": "700016",
    "billing_state": "WB",
}


class ExtractionContext:
    """Per-job container for all mutable state that was module-level in upi_extract.py.

    Usage::

        ctx = ExtractionContext(config={"bootstrap_country": "IN", ...})
        ctx.log("task started")
        state = ctx.load_proxy_state()
        ctx.register_proxy_for_redaction("http://user:pass-country=IN@host:8080")
    """

    # ── constructor ──────────────────────────────────────────────────────

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = dict(config or {})

        # Resolve script_dir early — everything else depends on it.
        script_dir = Path(config.get("script_dir") or ".").resolve()
        self.script_dir: Path = script_dir

        # Merge config with defaults.
        self._cfg: dict[str, Any] = dict(_DEFAULT_CONFIG)
        for key, value in config.items():
            if key in self._cfg or key.startswith("_"):
                self._cfg[key] = value

        # Derived paths.
        self.log_dir: Path = (
            Path(self._cfg["log_dir"]) if self._cfg["log_dir"]
            else script_dir / "logs"
        )
        self.dump_dir: Path = (
            Path(self._cfg["dump_dir"]) if self._cfg["dump_dir"]
            else script_dir / "dumps"
        )
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.dump_dir.mkdir(parents=True, exist_ok=True)

        # ── runtime mutable state ───────────────────────────────────────
        self.log_file: Path = self.log_dir / f"upi_{time.strftime('%Y%m%d-%H%M%S')}.log"
        self.dump_counter: int = 0

        # Proxy state (lazy-loaded from disk).
        self._proxy_state: dict[str, Any] | None = None

        # Locks — one per mutable collection, mirroring the original globals.
        self.proxy_state_lock: RLock = RLock()
        self.log_lock: RLock = RLock()
        self.dump_lock: RLock = RLock()
        self.proxy_file_lock: RLock = RLock()
        self.proxy_redaction_lock: RLock = RLock()

        # Proxy redaction — values that should be masked in log output.
        self.proxy_redaction_values: set[str] = set()

        # Thread-local log context (carries per-request prefix).
        self.log_context: local = local()

        # ── pre-proxy cache ─────────────────────────────────────────────
        self._pre_proxy: str | None = None  # resolved on first access

    # ── configuration accessors ──────────────────────────────────────────

    @property
    def bootstrap_country(self) -> str:
        return str(self._cfg.get("bootstrap_country", "IN")).upper()

    @property
    def promotion_countries(self) -> list[str]:
        value = self._cfg.get("promotion_countries", ["VN"])
        if isinstance(value, str):
            return [c.strip().upper() for c in value.split(",") if c.strip()]
        return [str(c).strip().upper() for c in value]

    @property
    def promotion_country(self) -> str:
        return self.promotion_countries[0] if self.promotion_countries else "VN"

    @property
    def provider_country(self) -> str:
        return str(self._cfg.get("provider_country", "IN")).upper()

    @property
    def provider_country_label(self) -> str:
        return str(self._cfg.get("provider_country_label", "IN")).upper()

    def cfg_bool(self, key: str, default: bool = False) -> bool:
        raw = self._cfg.get(key, default)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)

    def cfg_int(self, key: str, default: int, minimum: int = 1) -> int:
        raw = self._cfg.get(key, default)
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = default
        return max(minimum, val)

    def cfg_str(self, key: str, default: str = "") -> str:
        return str(self._cfg.get(key, default) or default)

    # ── proxy state key helpers ─────────────────────────────────────────

    @property
    def proxy_state_file(self) -> Path:
        raw = self._cfg.get("proxy_state_file", "")
        return Path(raw) if raw else self.script_dir / "proxy_state.json"

    @property
    def proxy_seed_file_path(self) -> Path:
        raw = self._cfg.get("proxy_seed_file", "")
        return Path(raw).expanduser() if raw else self.script_dir / "proxy_seeds.txt"

    # ── default proxy scheme ────────────────────────────────────────────

    def default_proxy_scheme(self) -> str:
        raw = self.cfg_str("default_proxy_scheme", "http").lower()
        raw = raw[:-3] if raw.endswith("://") else raw
        if raw in ("socks5", "socks5h"):
            return "socks5h"
        if raw in ("http", "https"):
            return raw
        return "http"

    # ── pre-proxy URL ───────────────────────────────────────────────────

    def pre_proxy_url(self) -> str:
        """本机前置代理：本机代理 → 文件代理 → 目标站。"""
        if self._pre_proxy is not None:
            return self._pre_proxy
        # Check the direct config key first.
        raw = self.cfg_str("pre_proxy", "").strip()
        if raw.lower() in {"", "0", "off", "none", "direct", "disabled"}:
            self._pre_proxy = ""
            return ""
        if raw:
            proxy = normalize_pre_proxy_url(raw)
            self.register_proxy_for_redaction(proxy)
            self._pre_proxy = proxy
            return proxy
        self._pre_proxy = ""
        return ""

    # ── proxy redaction ─────────────────────────────────────────────────

    def register_proxy_for_redaction(self, proxy: str) -> None:
        """Register a proxy URL (and its derived forms) for masking in log output."""
        raw = str(proxy or "").strip()
        if not raw:
            return
        scheme = self.default_proxy_scheme()
        normalized = normalize_proxy_url(raw, scheme)
        values = {raw}
        if normalized:
            values.add(normalized)
            decoded = unquote(normalized)
            values.add(decoded)
            parsed = urlsplit(decoded)
            if parsed.netloc:
                values.add(parsed.netloc)
            if parsed.hostname:
                host = parsed.hostname
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                try:
                    port = parsed.port
                except ValueError:
                    port = None
                values.add(f"{host}:{port}" if port else host)
        with self.proxy_redaction_lock:
            self.proxy_redaction_values.update(values)

    def redact_log_text(self, text: str) -> str:
        """Replace all registered proxy values in *text* with short labels."""
        text = str(text or "")
        with self.proxy_redaction_lock:
            values = sorted(self.proxy_redaction_values, key=len, reverse=True)
        for value in values:
            if value:
                try:
                    label = proxy_label(value)
                except (TypeError, ValueError):
                    label = f"proxy#{hashlib.sha256(value.encode()).hexdigest()[:10]}"
                if label == "direct":
                    label = f"proxy#{hashlib.sha256(value.encode()).hexdigest()[:10]}"
                text = text.replace(value, label)
        return text

    # ── logging ─────────────────────────────────────────────────────────

    def log(self, message: str, prefix: str = "") -> None:
        """Thread-safe log to stdout and the context's log file.

        Respects the thread-local ``log_context.prefix`` for per-request
        context (e.g. ``"[seed#1] "``).
        """
        context_prefix = getattr(self.log_context, "prefix", "")
        line = self.redact_log_text(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {prefix}{context_prefix}{message}"
        )
        with self.log_lock:
            print(line, flush=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    # ── HTTP dump ───────────────────────────────────────────────────────

    def _redact_text(self, text: str, limit: int | None = None) -> str:
        """Redact tokens and proxy values from a text blob, then truncate."""
        text = text or ""
        text = re.sub(r"(Bearer\s+)[A-Za-z0-9._=-]+", r"\1***", text)
        text = re.sub(r"(__Secure-next-auth\.session-token=)[^;\s]+", r"\1***", text)
        text = re.sub(
            r"(accessToken|access_token|sessionToken|token)(['\"]?\s*[:=]\s*['\"])[^'\"]+",
            r"\1\2***",
            text,
        )
        text = self.redact_log_text(text)
        if limit is None:
            limit = self.cfg_int("dump_limit", 6000, minimum=500)
        return text[:limit]

    def dump_http(
        self,
        response: Any | None,
        stage: str,
        request_body: Any = None,
        request_method: str = "",
        request_url: str = "",
        force: bool = False,
    ) -> None:
        """Write an HTTP request/response pair to a dump file for debugging.

        Respects the ``dump`` config flag unless *force* is True.
        """
        if not force and not self.cfg_bool("dump", False):
            return
        with self.dump_lock:
            self.dump_counter += 1
            name = f"{time.strftime('%Y%m%d-%H%M%S')}_{self.dump_counter:04d}_{stage}.txt"
        path = self.dump_dir / re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        lines = [
            f"stage: {stage}",
            f"request: {request_method} {request_url}",
            "",
            "request_body:",
            self._redact_text(
                json.dumps(request_body, ensure_ascii=False, indent=2)
                if request_body is not None
                else ""
            ),
            "",
        ]
        if response is not None:
            lines.extend(
                [
                    f"status: {response.status_code}",
                    f"url: {response.url}",
                    "",
                    "response:",
                    self._redact_text(response.text),
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")

    # ── proxy state management ───────────────────────────────────────────

    def load_proxy_state(self) -> dict[str, Any]:
        """Lazy-load proxy state from disk. Initialises default structure on first access."""
        with self.proxy_state_lock:
            if self._proxy_state is not None:
                return self._proxy_state
            path = self.proxy_state_file
            if not path.exists():
                self._proxy_state = {
                    "seed": {},
                    "checkout": {},
                    "promotion": {},
                    "provider": {},
                    "pair": {},
                }
                return self._proxy_state
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("seed", {})
            data.setdefault("checkout", {})
            data.setdefault("promotion", {})
            data.setdefault("provider", {})
            data.setdefault("pair", {})
            self._proxy_state = data
            return self._proxy_state

    def save_proxy_state(self) -> None:
        """Persist the in-memory proxy state to disk as sorted JSON."""
        with self.proxy_state_lock:
            if self._proxy_state is None:
                return
            path = self.proxy_state_file
            path.write_text(
                json.dumps(self._proxy_state, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    def proxy_record(self, group: str, proxy: str) -> dict[str, Any]:
        """Get-or-create a proxy record in *group*. Returns the record dict (empty on failure)."""
        with self.proxy_state_lock:
            state = self.load_proxy_state()
            group_state = state.setdefault(group, {})
            key = proxy_state_key(group, proxy)
            if not key:
                return {}
            record = group_state.setdefault(key, {})
            record.setdefault("success", 0)
            record.setdefault("fail", 0)
            return record

    def record_proxy_result(
        self, group: str, proxy: str, success: bool, reason: str = ""
    ) -> dict[str, Any]:
        """Record a success or failure for a proxy, updating counts and timestamps."""
        if not proxy or not self.cfg_bool("proxy_score", True):
            return {}
        record = self.proxy_record(group, proxy)
        if not record:
            return {}
        now = int(time.time())
        if success:
            record["success"] = int(record.get("success") or 0) + 1
            record["fail"] = 0
            record["last_success"] = now
            record["last_reason"] = "success"
        else:
            record["fail"] = int(record.get("fail") or 0) + 1
            record["last_fail"] = now
            record["last_reason"] = str(reason or "failed")[:160]
        self.save_proxy_state()
        return record

    def proxy_remove_after_fails(self) -> int:
        """How many consecutive failures before a proxy is removed from the seed file."""
        return self.cfg_int("proxy_remove_after_fails", 3)

    def is_reused_proxy_record(self, group: str, record: dict[str, Any]) -> bool:
        """Return True if this proxy record has at least one prior success."""
        return int(record.get("success") or 0) > 0

    def record_proxy_health_failure(self, group: str, proxy: str, reason: str) -> None:
        """Record a health-related failure. Removes the proxy if failures exceed threshold."""
        record = self.record_proxy_result(group, proxy, False, reason)
        fail_count = int(record.get("fail") or 0)
        remove_after = (
            self.proxy_remove_after_fails()
            if self.is_reused_proxy_record(group, record)
            else 1
        )
        if fail_count >= remove_after:
            self.remove_failed_proxy(group, proxy, reason)

    # ── zero cache ────────────────────────────────────────────────────────

    def checkout_zero_cache_ttl(self) -> int:
        """TTL (seconds) for cached zero-amount checkout results."""
        return self.cfg_int("zero_cache_ttl", 86400, minimum=0)

    def zero_cache_scheduling_enabled(self) -> bool:
        """Whether zero-cache-aware proxy scheduling is enabled."""
        return self.cfg_bool("zero_cache_scheduling", False)

    def checkout_zero_cache_status(self, proxy: str, country: str) -> tuple[str, int, int]:
        """Check whether a proxy has a cached zero-amount result for *country*.

        Returns ``(status, amount, checked_at)`` where status is "ok", "bad", or "".
        """
        if not proxy or not self.cfg_bool("zero_cache", True):
            return "", 0, 0
        record = self.proxy_record("seed", proxy)
        if not record:
            return "", 0, 0
        checked_at = int(record.get("zero_checked_at") or 0)
        if not checked_at:
            return "", 0, 0
        ttl = self.checkout_zero_cache_ttl()
        if ttl > 0 and int(time.time()) - checked_at > ttl:
            return "", 0, checked_at
        if normalize_country(str(record.get("zero_country") or country)) != normalize_country(country):
            return "", 0, checked_at
        amount = int(record.get("zero_amount") or 0)
        if record.get("zero_ok") is True:
            return "ok", amount, checked_at
        if record.get("zero_ok") is False:
            return "bad", amount, checked_at
        return "", amount, checked_at

    def record_checkout_zero_result(self, proxy: str, country: str, amount: int) -> None:
        """Store a zero-cache result for *proxy* in *country*."""
        if not proxy or not self.cfg_bool("zero_cache", True):
            return
        record = self.proxy_record("seed", proxy)
        if not record:
            return
        amount = int(amount or 0)
        record["zero_ok"] = amount == 0
        record["zero_amount"] = amount
        record["zero_country"] = normalize_country(country)
        record["zero_checked_at"] = int(time.time())
        if amount == 0:
            record["zero_success"] = int(record.get("zero_success") or 0) + 1
        self.save_proxy_state()

    # ── proxy pair scoring ─────────────────────────────────────────────────

    def record_proxy_pair_result(
        self, checkout_proxy: str, provider_proxy: str, success: bool, reason: str = ""
    ) -> None:
        """Record a checkout+provider pair result for pair-level scoring."""
        self.record_proxy_result("checkout", checkout_proxy, success, reason)
        self.record_proxy_result("provider", provider_proxy, success, reason)
        if not checkout_proxy or not provider_proxy or not self.cfg_bool("proxy_score", True):
            return
        key = proxy_pair_key(checkout_proxy, provider_proxy)
        if not key:
            return
        with self.proxy_state_lock:
            state = self.load_proxy_state()
            pair_state = state.setdefault("pair", {})
            record = pair_state.setdefault(
                key,
                {
                    "checkout": proxy_key(checkout_proxy),
                    "provider": proxy_key(provider_proxy),
                },
            )
            now = int(time.time())
            if success:
                record["success"] = int(record.get("success") or 0) + 1
                record["fail"] = 0
                record["last_success"] = now
                record["last_reason"] = "success"
            else:
                record["fail"] = int(record.get("fail") or 0) + 1
                record["last_fail"] = now
                record["last_reason"] = str(reason or "failed")[:160]
            self.save_proxy_state()

    def record_proxy_pair_approve_success(
        self, checkout_proxy: str, provider_proxy: str, approve_proxy: str
    ) -> None:
        """Record a successful approve for a checkout+provider pair."""
        if (
            not checkout_proxy
            or not provider_proxy
            or not approve_proxy
            or not self.cfg_bool("proxy_score", True)
        ):
            return
        key = proxy_pair_key(checkout_proxy, provider_proxy)
        approve_key = proxy_key(approve_proxy)
        if not key or not approve_key:
            return
        self.record_proxy_result("provider", approve_proxy, True, "approve_success")
        with self.proxy_state_lock:
            state = self.load_proxy_state()
            pair_state = state.setdefault("pair", {})
            record = pair_state.setdefault(
                key,
                {
                    "checkout": proxy_key(checkout_proxy),
                    "provider": proxy_key(provider_proxy),
                },
            )
            now = int(time.time())
            record["approve"] = approve_key
            record["approve_success"] = int(record.get("approve_success") or 0) + 1
            record["approve_last_success"] = now
            record["approve_last_reason"] = "success"
            self.save_proxy_state()

    def successful_approve_preferences(
        self, checkout_proxy: str, provider_proxy: str, approve_pool: list[str]
    ) -> list[str]:
        """Return the historically successful approve proxy for a checkout+provider pair."""
        if not self.cfg_bool("proxy_score", True):
            return []
        pair_state = self.load_proxy_state().get("pair", {})
        if not isinstance(pair_state, dict):
            return []
        record = pair_state.get(proxy_pair_key(checkout_proxy, provider_proxy))
        if not isinstance(record, dict):
            return []
        approve_key = str(record.get("approve") or "")
        if not approve_key:
            return []
        approve_by_key = {proxy_key(proxy): proxy for proxy in approve_pool}
        approve_proxy = approve_by_key.get(approve_key)
        return [approve_proxy] if approve_proxy else []

    # ── failure routing ───────────────────────────────────────────────────

    def record_failure_by_stage(
        self,
        reason: str,
        checkout_proxy: str,
        provider_proxy: str,
        promotion_proxy: str = "",
    ) -> None:
        """Route a failure to the correct proxy group based on the failure reason."""

        def record_seed_failure(proxy: str) -> None:
            if not proxy:
                return
            if is_direct_remove_proxy_error(reason):
                self.remove_failed_proxy("seed", proxy, reason)
                self.record_proxy_result("seed", proxy, False, reason)
            elif is_proxy_health_failure(reason):
                self.record_proxy_health_failure("seed", proxy, reason)
            else:
                self.record_proxy_result("seed", proxy, False, reason)

        if "checkout 阶段失败" in reason or "checkout 创建失败" in reason:
            record_seed_failure(checkout_proxy)
            return
        if is_upi_unavailable_error(reason):
            return
        if "0 元优惠未生效" in reason:
            return
        if "approve blocked" in reason:
            return
        if "promotion 阶段失败" in reason or "checkout/update" in reason:
            record_seed_failure(promotion_proxy)
            return
        record_seed_failure(provider_proxy)

    # ── proxy ordering ────────────────────────────────────────────────────

    def order_proxy_group(self, group: str, proxies: list[str]) -> list[str]:
        """Sort and filter a list of proxies by success/fail history.

        Failed proxies in cooldown are skipped. Proxies with zero-cache "bad"
        results are skipped when zero_cache_skip_bad is enabled. Remaining
        proxies are ranked by: zero-cache hit > success count > last success >
        negative fail count > negative last fail.
        """
        if not self.cfg_bool("proxy_score", True):
            return proxies
        state = self.load_proxy_state().get(group, {})
        skip_failed = self.cfg_bool("proxy_skip_failed", True)
        fail_threshold = self.cfg_int("proxy_fail_skip_after", 1)
        fail_cooldown = self.cfg_int("proxy_fail_cooldown", 180, minimum=0)
        zero_ttl = self.checkout_zero_cache_ttl()
        zero_scheduling = self.zero_cache_scheduling_enabled()
        zero_skip_bad = self.cfg_bool("zero_cache_skip_bad", True)
        now = int(time.time())
        kept: list[str] = []
        cooldown_skipped = 0
        zero_skipped = 0
        zero_seen = 0
        success_seen = 0
        for proxy in proxies:
            record = (
                state.get(proxy_state_key(group, proxy), {})
                if isinstance(state, dict)
                else {}
            )
            success_count = int(record.get("success") or 0)
            fail_count = int(record.get("fail") or 0)
            last_fail = int(record.get("last_fail") or 0)
            if success_count > 0:
                success_seen += 1
            zero_checked_at = int(record.get("zero_checked_at") or 0)
            zero_cache_valid = zero_checked_at and (
                zero_ttl <= 0 or now - zero_checked_at <= zero_ttl
            )
            if (
                group == "checkout"
                and zero_scheduling
                and zero_cache_valid
                and record.get("zero_ok") is True
            ):
                zero_seen += 1
            if (
                group == "checkout"
                and zero_scheduling
                and zero_skip_bad
                and zero_cache_valid
                and record.get("zero_ok") is False
            ):
                zero_skipped += 1
                continue
            if skip_failed and fail_count >= fail_threshold:
                in_cooldown = (
                    fail_cooldown <= 0
                    or not last_fail
                    or now - last_fail <= fail_cooldown
                )
                if in_cooldown:
                    cooldown_skipped += 1
                    continue
            kept.append(proxy)

        if not kept and proxies:
            self.log(f"{group} 代理状态过滤后为空，已全部跳过", "[WARN] ")

        def rank(proxy: str) -> tuple[int, int, int, int, int]:
            record = (
                state.get(proxy_state_key(group, proxy), {})
                if isinstance(state, dict)
                else {}
            )
            zero_checked_at = int(record.get("zero_checked_at") or 0)
            zero_cache_valid = zero_checked_at and (
                zero_ttl <= 0 or now - zero_checked_at <= zero_ttl
            )
            zero_rank = (
                1
                if group == "checkout"
                and zero_scheduling
                and zero_cache_valid
                and record.get("zero_ok") is True
                else 0
            )
            return (
                zero_rank,
                int(record.get("success") or 0),
                int(record.get("last_success") or 0),
                -int(record.get("fail") or 0),
                -int(record.get("last_fail") or 0),
            )

        ordered = sorted(kept, key=rank, reverse=True)
        if cooldown_skipped or success_seen or zero_seen or zero_skipped:
            self.log(
                f"{group} 代理状态: 成功优先={success_seen}，0元命中={zero_seen}，"
                f"冷却跳过={cooldown_skipped}，0元失败跳过={zero_skipped}"
            )
        return ordered

    # ── proxy seed loading ────────────────────────────────────────────────

    def parse_proxy_seed_lines(self, lines: list[str]) -> list[str]:
        """Normalise proxy seeds from text lines.

        Supported formats:
        - one proxy per line
        - comma-separated proxies
        - blank lines and lines beginning with "#" are ignored
        """
        proxies: list[str] = []
        for line in lines:
            line = str(line or "").strip().lstrip("\ufeff")
            if not line or line.startswith("#"):
                continue
            for raw_proxy in line.split(","):
                raw_proxy = raw_proxy.strip()
                if not raw_proxy or raw_proxy.startswith("#"):
                    continue
                self.register_proxy_for_redaction(raw_proxy)
                proxy = normalize_proxy_url(raw_proxy, self.default_proxy_scheme())
                if proxy:
                    proxies.append(proxy)
        return proxies

    def load_inline_proxy_seeds(self) -> list[str]:
        """Load per-job proxy seeds from config without touching the seed file."""
        chain_seeds = self.load_inline_proxy_chain_seeds()
        if chain_seeds:
            random.shuffle(chain_seeds)
            return chain_seeds

        raw = self._cfg.get("proxy_seeds") or []
        if isinstance(raw, str):
            lines = raw.splitlines()
        elif isinstance(raw, list):
            lines = [str(item) for item in raw]
        else:
            lines = []
        proxies = self.parse_proxy_seed_lines(lines)
        random.shuffle(proxies)
        return proxies

    def load_inline_proxy_chain_seeds(self) -> list[str]:
        """Load per-job explicit checkout/promotion/provider proxy chains."""
        raw = self._cfg.get("proxy_seed_chains") or []
        if not isinstance(raw, list):
            return []
        seeds: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            checkout = normalize_proxy_url(str(item.get("checkout") or ""), self.default_proxy_scheme())
            promotion = normalize_proxy_url(str(item.get("promotion") or ""), self.default_proxy_scheme())
            provider = normalize_proxy_url(str(item.get("provider") or ""), self.default_proxy_scheme())
            seed = make_proxy_chain_seed(checkout, promotion, provider)
            if not seed:
                continue
            for proxy in (checkout, promotion, provider):
                self.register_proxy_for_redaction(proxy)
            self.register_proxy_for_redaction(seed)
            seeds.append(seed)
        return seeds

    def load_proxy_file(self, path: Path) -> list[str]:
        """Read proxy seeds from a file, normalise URLs, and shuffle."""
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            proxies = self.parse_proxy_seed_lines(list(f))
        random.shuffle(proxies)
        return proxies

    def unique_proxy_seeds(self, proxy_seeds: list[str]) -> list[str]:
        """Deduplicate proxy seeds by their chain key (removes sticky-session duplicates)."""
        seen: set[str] = set()
        unique: list[str] = []
        duplicates = 0
        for proxy_seed in proxy_seeds:
            chain_key = proxy_chain_key(proxy_seed)
            if not chain_key or chain_key in seen:
                duplicates += 1
                continue
            seen.add(chain_key)
            unique.append(proxy_seed)
        if duplicates:
            self.log(
                f"代理 Seed 去重: 忽略相同 sticky session {duplicates} 条", "[WARN] "
            )
        return unique

    def load_proxy_seeds(self) -> list[str]:
        """Full seed-loading pipeline: read, deduplicate, prune, order.

        Raises RuntimeError if the seed file is missing, empty, or all seeds
        are in failure cooldown.
        """
        inline_proxy_seeds = self.load_inline_proxy_seeds()
        if inline_proxy_seeds:
            proxy_seeds = self.unique_proxy_seeds(inline_proxy_seeds)
            self.log(f"加载本次任务自定义代理 Seed {len(proxy_seeds)} 条")
        else:
            path = self.proxy_seed_file_path
            if not path.is_file():
                raise RuntimeError("代理 Seed 文件不存在")
            proxy_seeds = self.unique_proxy_seeds(self.load_proxy_file(path))
        if not proxy_seeds:
            raise RuntimeError("代理 Seed 为空")
        self.prune_proxy_seed_state(proxy_seeds)
        proxy_seeds = self.order_proxy_group("seed", proxy_seeds)
        if not proxy_seeds:
            raise RuntimeError("代理 Seed 已全部处于失败冷却")
        self.log(f"加载代理 Seed {len(proxy_seeds)} 条")
        if self.cfg_bool("use_promotion_stage", False):
            promotion_chain = " → ".join(self.promotion_countries)
            self.log(
                "严格代理策略: 每轮取一条 seed，派生 "
                f"{self.bootstrap_country} Checkout → {promotion_chain} checkout/update → "
                f"{self.provider_country} Stripe/UPI/approve"
            )
        else:
            self.log(
                "严格代理策略: 每轮取一条 seed，派生 "
                f"{self.bootstrap_country} Checkout → {self.provider_country} Stripe/UPI/approve"
            )
        self.log(f"裸代理默认协议: {self.default_proxy_scheme()}://")
        self.log(f"本机前置代理: {proxy_short(self.pre_proxy_url())}")
        return proxy_seeds

    # ── proxy state pruning ───────────────────────────────────────────────

    def prune_proxy_seed_state(self, proxy_seeds: list[str]) -> None:
        """Remove stale seed entries that are no longer in the current seed list."""
        with self.proxy_state_lock:
            state = self.load_proxy_state()
            seed_state = state.setdefault("seed", {})
            active_keys = {
                proxy_chain_key(proxy) for proxy in proxy_seeds if proxy_chain_key(proxy)
            }
            stale_keys = [key for key in seed_state if key not in active_keys]
            for key in stale_keys:
                del seed_state[key]
            if stale_keys:
                self.save_proxy_state()
        if stale_keys:
            self.log(f"Seed 代理状态清理完成: {len(stale_keys)}")

    def prune_proxy_state(
        self,
        checkout_proxies: list[str],
        promotion_proxies: list[str],
        provider_proxies: list[str],
    ) -> None:
        """Remove stale proxy state entries not in the active proxy pools."""
        removed_counts: dict[str, int] = {}
        with self.proxy_state_lock:
            state = self.load_proxy_state()
            active_keys_by_group: dict[str, set[str]] = {}
            for group, proxies in (
                ("checkout", checkout_proxies),
                ("promotion", promotion_proxies),
                ("provider", provider_proxies),
            ):
                group_state = state.get(group)
                if not isinstance(group_state, dict):
                    continue
                active_keys = {proxy_key(proxy) for proxy in proxies if proxy}
                active_keys_by_group[group] = active_keys
                stale_keys = [key for key in group_state if key not in active_keys]
                for key in stale_keys:
                    del group_state[key]
                if stale_keys:
                    removed_counts[group] = len(stale_keys)
            pair_state = state.get("pair")
            if isinstance(pair_state, dict):
                active_checkout = active_keys_by_group.get("checkout", set())
                active_provider = active_keys_by_group.get("provider", set())
                stale_pair_keys = [
                    key
                    for key, record in pair_state.items()
                    if not isinstance(record, dict)
                    or record.get("checkout") not in active_checkout
                    or record.get("provider") not in active_provider
                ]
                for key in stale_pair_keys:
                    del pair_state[key]
                if stale_pair_keys:
                    removed_counts["pair"] = len(stale_pair_keys)
            if removed_counts:
                self.save_proxy_state()
        if removed_counts:
            summary = ", ".join(
                f"{group}={count}" for group, count in removed_counts.items()
            )
            self.log(f"代理状态清理完成: {summary}")

    # ── failed proxy removal ──────────────────────────────────────────────

    def remove_failed_proxy(self, group: str, proxy: str, reason: str) -> bool:
        """Remove a single failed proxy from the seed file."""
        return self.remove_failed_proxies(group, [(proxy, reason)]) > 0

    def remove_failed_proxies(self, group: str, failures: list[tuple[str, str]]) -> int:
        """Remove failed proxies from the seed file and quarantine them.

        Uses atomic ``os.replace()`` with a ``shutil.copy2 + unlink`` fallback
        for Docker bind-mount filesystems where rename across devices fails.
        Returns the number of proxies removed.
        """
        if not failures or not self.cfg_bool("proxy_remove_failed", True):
            return 0
        for proxy, _reason in failures:
            self.register_proxy_for_redaction(proxy)
        path = self.proxy_seed_file_path
        if not path.is_file():
            return 0
        reasons = {
            proxy_chain_key(proxy): reason
            for proxy, reason in failures
            if proxy_chain_key(proxy)
        }
        if not reasons:
            return 0
        with self.proxy_file_lock:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            removed = [
                line for line in lines if proxy_chain_key(line) in reasons
            ]
            if not removed:
                return 0
            kept = [
                line for line in lines if proxy_chain_key(line) not in reasons
            ]
            quarantine = self.script_dir / "removed_proxies.jsonl"
            with open(quarantine, "a", encoding="utf-8") as f:
                for line in removed:
                    chain_key = proxy_chain_key(line)
                    f.write(
                        json.dumps(
                            {
                                "time": int(time.time()),
                                "group": group,
                                "proxy": proxy_label(line.strip()),
                                "reason": self.redact_log_text(
                                    str(reasons.get(chain_key) or "")
                                )[:300],
                                "source": path.name,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            temp_path = path.with_name(f".{path.name}.tmp")
            temp_path.write_text("".join(kept), encoding="utf-8")
            try:
                os.replace(temp_path, path)
            except OSError:
                # Docker bind-mount / cross-device fallback
                shutil.copy2(temp_path, path)
                temp_path.unlink(missing_ok=True)
        return len(removed)

    # ── serialisation ───────────────────────────────────────────────────

    def to_config_dict(self) -> dict[str, Any]:
        """Return a picklable dict of *configuration only* (no runtime state).

        This is the payload sent to worker processes via ProcessPoolExecutor.
        The worker calls ``ExtractionContext(config=ctx.to_config_dict())``
        to reconstruct a fresh instance.
        """
        # Export only the config layer — no locks, counters, proxy state, etc.
        export: dict[str, Any] = {}

        # Paths (convert to str for serialisation).
        export["script_dir"] = str(self.script_dir)
        export["log_dir"] = str(self.log_dir)
        export["dump_dir"] = str(self.dump_dir)
        export["proxy_seed_file"] = str(self.proxy_seed_file_path)
        export["proxy_state_file"] = str(self.proxy_state_file)

        # Configuration values.
        for key, default in _DEFAULT_CONFIG.items():
            if key in ("log_dir", "dump_dir", "script_dir",
                        "proxy_seed_file", "proxy_state_file"):
                continue  # already exported above
            export[key] = self._cfg.get(key, default)

        return export

    def to_config_json(self) -> str:
        return json.dumps(self.to_config_dict(), ensure_ascii=False)

    # ── __repr__ ────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"ExtractionContext("
            f"script={self.script_dir.name}, "
            f"countries={self.bootstrap_country}/{self.promotion_country}/{self.provider_country}"
            f")"
        )
