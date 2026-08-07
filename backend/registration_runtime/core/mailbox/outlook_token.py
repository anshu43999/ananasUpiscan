from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from core.base_sms import extract_verification_code
from core.proxy_utils import build_requests_proxy_config


_GRAPH_POLL_LIMIT_DEFAULT = 20
_graph_poll_sem_lock = threading.Lock()
_graph_poll_sem: threading.Semaphore | None = None
_graph_poll_sem_limit = 0


@dataclass(frozen=True)
class OutlookTokenAccount:
    email: str
    password: str
    client_id: str
    refresh_token: str


class OutlookTokenMailbox:
    """Read OpenAI verification codes from Outlook through Microsoft Graph."""

    def __init__(self, config: dict[str, Any], *, log_fn: Callable[[str], None] | None = None):
        self.config = config if isinstance(config, dict) else {}
        self.log_fn = log_fn or (lambda _message: None)

    def log(self, message: str) -> None:
        self.log_fn(message)

    @staticmethod
    def _graph_poll_limit(config: dict[str, Any] | None) -> int:
        raw = (config or {}).get("outlook_graph_max_concurrent") or (config or {}).get("email_otp_graph_max_concurrent")
        try:
            value = int(raw) if raw not in (None, "") else _GRAPH_POLL_LIMIT_DEFAULT
        except (TypeError, ValueError):
            value = _GRAPH_POLL_LIMIT_DEFAULT
        return max(1, min(100, value))

    @classmethod
    def _graph_poll_semaphore(cls, config: dict[str, Any] | None) -> threading.Semaphore:
        global _graph_poll_sem, _graph_poll_sem_limit
        limit = cls._graph_poll_limit(config)
        with _graph_poll_sem_lock:
            if _graph_poll_sem is None or _graph_poll_sem_limit != limit:
                _graph_poll_sem = threading.Semaphore(limit)
                _graph_poll_sem_limit = limit
            return _graph_poll_sem

    def _pool_state_path(self) -> Path:
        configured = str(self.config.get("outlook_pool_state_file") or "").strip()
        if configured:
            path = Path(configured)
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "outlook_pool_state.jsonl"

    def _append_pool_event(self, email: str, status: str, reason: str = "") -> None:
        normalized = str(email or "").strip().lower()
        if not normalized:
            return
        event = {
            "email": normalized,
            "status": status,
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        path = self._pool_state_path()
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _load_pool_events(self) -> list[dict[str, Any]]:
        path = self._pool_state_path()
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    def _load_used_emails(self) -> set[str]:
        used: set[str] = set()
        for event in self._load_pool_events():
            email = str(event.get("email") or "").strip().lower()
            status = str(event.get("status") or "").strip().lower()
            if email and status in {"used", "completed", "registered", "disabled"}:
                used.add(email)
        return used

    def _retryable_failures(self, email: str) -> int:
        normalized = str(email or "").strip().lower()
        if not normalized:
            return 0
        count = 0
        for event in self._load_pool_events():
            if str(event.get("email") or "").strip().lower() != normalized:
                continue
            status = str(event.get("status") or "").strip().lower()
            if status in {"used", "completed", "registered"}:
                return 999999
            if status in {"failed_retryable", "cooldown", "otp_timeout", "wrong_otp"}:
                count += 1
        return count

    def _is_cooled_down(self, email: str) -> bool:
        failures = self._retryable_failures(email)
        limit = int(self.config.get("outlook_failed_retryable_limit", 2) or 2)
        if failures < limit:
            return False
        hours = float(self.config.get("outlook_cooldown_hours", 24) or 24)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        latest: datetime | None = None
        for event in self._load_pool_events():
            if str(event.get("email") or "").strip().lower() != str(email or "").strip().lower():
                continue
            raw_ts = str(event.get("updated_at") or "")
            try:
                ts = datetime.fromisoformat(raw_ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            latest = ts if latest is None or ts > latest else latest
        return latest is None or latest > cutoff

    def _has_prepared_outlook_lease(self, email: str) -> bool:
        leases = self.config.get("resource_leases") if isinstance(self.config.get("resource_leases"), list) else []
        target = str(email or "").strip().lower()
        return any(
            isinstance(item, dict)
            and str(item.get("type") or "").strip().lower() == "email"
            and str(item.get("provider") or "").strip().lower() == "outlook_token"
            and str(item.get("key") or "").strip().lower() == target
            for item in leases
        )

    def candidates(self, email: str = "", *, include_used: bool = False) -> list[OutlookTokenAccount]:
        target = str(email or self.config.get("outlook_email") or "").strip().lower()
        configured_email = str(self.config.get("outlook_email") or "").strip()
        configured_password = str(self.config.get("outlook_password") or "").strip()
        configured_client_id = str(self.config.get("outlook_client_id") or self.config.get("oauth_client_id") or "").strip()
        configured_refresh_token = str(self.config.get("outlook_refresh_token") or "").strip()
        used = set() if include_used else self._load_used_emails()
        candidates: list[OutlookTokenAccount] = []

        if configured_email and configured_client_id and configured_refresh_token:
            key = configured_email.lower()
            lease_override = self._has_prepared_outlook_lease(key)
            if (not target or key == target) and (include_used or lease_override or (key not in used and not self._is_cooled_down(key))):
                candidates.append(OutlookTokenAccount(configured_email, configured_password, configured_client_id, configured_refresh_token))

        paths: list[Path] = []
        configured_order = str(self.config.get("outlook_token_order_file") or "").strip()
        if configured_order:
            paths.append(Path(configured_order))
        paths.append(Path("outlook_accounts_token.txt"))

        seen = {item.email.lower() for item in candidates}
        for path in paths:
            if not path.exists():
                continue
            for raw_row in path.read_text(encoding="utf-8-sig").splitlines():
                parts = [part.strip() for part in raw_row.strip().split("----")]
                if len(parts) != 4:
                    continue
                candidate_email, candidate_password, client_id, refresh_token = parts
                key = candidate_email.lower()
                if "@" not in candidate_email or not client_id or not refresh_token or key in seen:
                    continue
                if not include_used and (key in used or self._is_cooled_down(key)):
                    continue
                if not target or key == target:
                    candidates.append(OutlookTokenAccount(candidate_email, candidate_password, client_id, refresh_token))
                    seen.add(key)
        return candidates

    def first(self, email: str = "", *, include_used: bool = False) -> OutlookTokenAccount:
        candidates = self.candidates(email, include_used=include_used)
        if not candidates:
            raise RuntimeError("No available Outlook token mailbox; import email----password----client_id----refresh_token first.")
        return candidates[0]

    @staticmethod
    def _is_dead_graph_token_error(message: str) -> bool:
        text = str(message or "").lower()
        markers = (
            "invalid_grant",
            "aadsts70000",
            "aadsts50173",
            "aadsts50076",
            "compromised",
            "interaction_required",
            "refresh token has expired",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _is_graph_proxy_transport_error(message: str) -> bool:
        text = str(message or "").lower()
        markers = (
            "network ",
            "ssl",
            "unexpected_eof",
            "max retries exceeded",
            "socks",
            "connection pool",
            "timed out",
            "timeout",
            "connection reset",
            "connection aborted",
            "proxyerror",
            "tunnel connection failed",
        )
        return any(marker in text for marker in markers)

    def _resolve_graph_proxy_url(self) -> str:
        for key in ("mailat_protocol_proxy", "lajiao_proxy_credentials", "proxy", "outlook_graph_proxy", "email_otp_proxy"):
            value = str(self.config.get(key) or "").strip()
            if not value:
                continue
            line = next((part.strip() for part in value.replace("\r", "\n").split("\n") if part.strip()), "")
            if not line:
                continue
            if "://" not in line:
                line = f"socks5h://{line}"
            lowered = line.lower()
            if "127.0.0.1" in lowered or "localhost" in lowered or "proxy.local" in lowered or "[::1]" in lowered:
                continue
            return line
        return ""

    def _graph_proxy_candidates(self) -> list[str]:
        raw = self._resolve_graph_proxy_url()
        if not raw:
            return []
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(raw if "://" in raw else f"socks5h://{raw}")
        scheme = (parts.scheme or "socks5h").lower()
        if scheme == "socks5":
            scheme = "socks5h"
        hostport = parts.netloc or parts.path
        if not hostport:
            return []
        candidates: list[str] = []
        for candidate_scheme in (scheme, "socks5h", "http"):
            candidate = urlunsplit((candidate_scheme, hostport, "", "", ""))
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _proxy_chain(self) -> list[dict[str, str] | None]:
        chain: list[dict[str, str] | None] = []
        for candidate in self._graph_proxy_candidates():
            proxies = build_requests_proxy_config(candidate)
            if proxies and proxies not in chain:
                chain.append(proxies)
        chain.append(None)
        return chain

    def _poll_interval_seconds(self) -> float:
        try:
            value = float(self.config.get("email_otp_poll_interval") or 3.0)
        except (TypeError, ValueError):
            value = 3.0
        return max(1.0, min(30.0, value))

    def refresh_graph_access_token(self, client_id: str, refresh_token: str, *, proxies: dict[str, str] | None | object = ...) -> str:
        def once(use_proxies: dict[str, str] | None) -> str:
            session = requests.Session()
            session.trust_env = False
            try:
                last_error = ""
                for scope in ("https://graph.microsoft.com/.default offline_access", "https://graph.microsoft.com/Mail.Read offline_access"):
                    try:
                        response = session.post(
                            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
                            data={
                                "client_id": client_id,
                                "grant_type": "refresh_token",
                                "refresh_token": refresh_token,
                                "scope": scope,
                            },
                            timeout=30,
                            proxies=use_proxies,
                        )
                    except requests.RequestException as exc:
                        last_error = f"network {exc}"
                        continue
                    data = response.json() if response.content else {}
                    token = str(data.get("access_token") or "")
                    if token:
                        return token
                    last_error = f"{response.status_code} {data.get('error')} {str(data.get('error_description') or '')[:160]}"
                    if self._is_dead_graph_token_error(last_error):
                        break
                raise RuntimeError(f"Outlook Graph token refresh failed: {last_error}")
            finally:
                session.close()

        chain = [proxies if isinstance(proxies, dict) or proxies is None else None] if proxies is not ... else self._proxy_chain()
        last_exc: Exception | None = None
        for index, use_proxies in enumerate(chain):
            try:
                return once(use_proxies)
            except RuntimeError as exc:
                last_exc = exc
                message = str(exc)
                if self._is_dead_graph_token_error(message):
                    self.mark_disabled("", reason=message[:240])
                    raise
                if use_proxies is not None and not self._is_graph_proxy_transport_error(message):
                    raise
                if index + 1 < len(chain):
                    label = "direct" if use_proxies is None else str((use_proxies or {}).get("https") or "")[:70]
                    self.log(f"Outlook Graph token route failed ({label}), trying next route: {message[:140]}")
        raise RuntimeError(str(last_exc or "Outlook Graph token refresh failed"))

    def _list_graph_messages(self, access_token: str, *, proxies: dict[str, str] | None | object = ...) -> list[dict[str, Any]]:
        def once(use_proxies: dict[str, str] | None) -> list[dict[str, Any]]:
            session = requests.Session()
            session.trust_env = False
            try:
                response = session.get(
                    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Prefer": 'outlook.body-content-type="text"',
                    },
                    params={
                        "$top": "50",
                        "$select": "from,subject,body,receivedDateTime",
                        "$orderby": "receivedDateTime desc",
                    },
                    timeout=30,
                    proxies=use_proxies,
                )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise RuntimeError(f"Outlook Graph inbox response is not JSON: HTTP {response.status_code}") from exc
            except requests.RequestException as exc:
                raise RuntimeError(f"Outlook Graph inbox network failed: {exc}") from exc
            finally:
                session.close()

            if not isinstance(payload, dict):
                raise RuntimeError(f"Outlook Graph inbox response has invalid shape: HTTP {response.status_code}")
            if response.status_code != 200:
                error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                code = str(error.get("code") or "unknown_error")
                message = str(error.get("message") or "")[:160]
                raise RuntimeError(f"Outlook Graph inbox read failed: HTTP {response.status_code} {code} {message}".rstrip())
            messages = payload.get("value")
            if not isinstance(messages, list):
                raise RuntimeError("Outlook Graph inbox response missing message list")
            return [item for item in messages if isinstance(item, dict)]

        chain = [proxies if isinstance(proxies, dict) or proxies is None else None] if proxies is not ... else self._proxy_chain()
        last_exc: Exception | None = None
        for index, use_proxies in enumerate(chain):
            try:
                return once(use_proxies)
            except RuntimeError as exc:
                last_exc = exc
                message = str(exc)
                if use_proxies is not None and not self._is_graph_proxy_transport_error(message):
                    raise
                if index + 1 < len(chain):
                    label = "direct" if use_proxies is None else str((use_proxies or {}).get("https") or "")[:70]
                    self.log(f"Outlook Graph inbox route failed ({label}), trying next route: {message[:140]}")
        raise RuntimeError(str(last_exc or "Outlook Graph inbox read failed"))

    @staticmethod
    def _parse_received_at(value: Any) -> datetime | None:
        try:
            raw = str(value or "").strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            received_at = datetime.fromisoformat(raw)
            if received_at.tzinfo is None:
                return received_at.replace(tzinfo=timezone.utc)
            return received_at.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_openai_code_from_text(text: str) -> str:
        return extract_verification_code(text, expected_lengths=(6,))

    def wait_for_openai_code(
        self,
        account: OutlookTokenAccount,
        *,
        timeout: int = 180,
        not_before: datetime | None = None,
        reject_codes: set[str] | None = None,
    ) -> str:
        deadline = time.time() + max(15, int(timeout or 180))
        started_at = not_before or (datetime.now(timezone.utc) - timedelta(seconds=30))
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        else:
            started_at = started_at.astimezone(timezone.utc)
        rejected = {str(code).strip() for code in (reject_codes or set()) if str(code).strip()}
        poll_interval = self._poll_interval_seconds()
        semaphore = self._graph_poll_semaphore(self.config)
        access_token = ""
        consecutive_errors = 0
        self.log(
            f"Using Outlook Graph mailbox {account.email}, since={started_at.isoformat(timespec='seconds')}, "
            f"concurrency={self._graph_poll_limit(self.config)}"
        )

        while time.time() < deadline:
            acquired = semaphore.acquire(timeout=max(1.0, min(15.0, deadline - time.time())))
            if not acquired:
                time.sleep(0.2)
                continue
            try:
                if not access_token:
                    try:
                        access_token = self.refresh_graph_access_token(account.client_id, account.refresh_token)
                    except Exception as exc:
                        message = str(exc)
                        if self._is_dead_graph_token_error(message):
                            self.mark_disabled(account.email, reason=message[:240])
                            raise RuntimeError(f"Outlook Graph refresh token is permanently invalid: {message}") from exc
                        consecutive_errors += 1
                        self.log(f"Outlook Graph token refresh error: {message[:160]}")
                        time.sleep(min(15.0, poll_interval * (1 + min(5, consecutive_errors))))
                        continue

                try:
                    messages = self._list_graph_messages(access_token)
                    consecutive_errors = 0
                except Exception as exc:
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        access_token = ""
                    self.log(f"Outlook Graph inbox read error: {str(exc)[:160]}")
                    time.sleep(min(15.0, poll_interval * (1 + min(5, consecutive_errors))))
                    continue

                best_code = ""
                best_received: datetime | None = None
                for message in messages:
                    sender = str(((message.get("from") or {}).get("emailAddress") or {}).get("address") or "")
                    subject = str(message.get("subject") or "")
                    body = str((message.get("body") or {}).get("content") or "")
                    received_at = self._parse_received_at(message.get("receivedDateTime"))
                    searchable = f"{sender} {subject} {body[:1000]}"
                    if not re.search(r"openai|chatgpt", searchable, flags=re.IGNORECASE):
                        continue
                    if received_at and received_at < started_at:
                        continue
                    code = self._extract_openai_code_from_text(body)
                    if not code or code in rejected:
                        continue
                    if best_received is None or (received_at and received_at > best_received):
                        best_code = code
                        best_received = received_at or datetime.now(timezone.utc)
                if best_code:
                    self.log(f"Outlook Graph verification code found: {best_code}")
                    return best_code
            finally:
                semaphore.release()
            time.sleep(poll_interval)
        raise TimeoutError(f"Timed out waiting for Outlook Graph verification code after {timeout}s")

    def mark_used(self, email: str, reason: str = "registered") -> None:
        self._append_pool_event(email, "registered", reason)

    def mark_cooldown(self, email: str, reason: str) -> None:
        self._append_pool_event(email, "failed_retryable", reason)

    def mark_disabled(self, email: str, reason: str = "graph_token_dead") -> None:
        if email:
            self._append_pool_event(email, "disabled", reason)

