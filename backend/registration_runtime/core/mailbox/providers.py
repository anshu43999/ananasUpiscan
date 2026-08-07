from __future__ import annotations

import random
import re
import string
import time
from typing import Any

import requests

from core.base_sms import extract_verification_code
from core.mailbox.forwarded_domain import ForwardedDomainMailbox, MailboxAccount


class ICloudPrivacyMailbox(ForwardedDomainMailbox):
    """Fixed iCloud Hide My Email alias with OTP delivered to a configured IMAP inbox."""

    def __init__(self, order_text: str = "", code_url: str = "", mail_url: str = "", inbox_url: str = "", proxy: str | None = None, poll_interval: int = 3, **kwargs: Any):
        super().__init__(domain="icloud.com", **kwargs)
        self.order_text = str(order_text or "")
        self.code_url = str(code_url or "").strip()
        self.mail_url = str(mail_url or "").strip()
        self.inbox_url = str(inbox_url or "").strip()
        self.poll_interval = max(1, int(poll_interval or 3))
        self.session = requests.Session()
        self.session.trust_env = False
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ICloudPrivacyMailbox":
        return cls(
            order_text=str(config.get("icloud_privacy_order_text") or config.get("email") or config.get("icloud_privacy_email") or ""),
            code_url=str(config.get("mailbox_code_url") or ""),
            mail_url=str(config.get("mailbox_mail_url") or ""),
            inbox_url=str(config.get("mailbox_inbox_url") or ""),
            proxy=str(config.get("mailbox_proxy") or config.get("proxy") or "") or None,
            poll_interval=int(config.get("email_otp_poll_interval") or 3),
            imap_user=str(config.get("mailbox_imap_user") or ""),
            imap_pass=str(config.get("mailbox_imap_pass") or ""),
            imap_host=str(config.get("mailbox_imap_host") or ""),
            imap_port=int(config.get("mailbox_imap_port") or 993),
        )

    def _rows(self) -> list[str]:
        rows: list[str] = []
        for line in self.order_text.replace("\r", "\n").split("\n"):
            text = line.strip().lstrip("\ufeff")
            if not text:
                continue
            email = text.split("----", 1)[0].strip().lower()
            if "@" in email:
                rows.append(email)
        return rows

    def create_account(self) -> MailboxAccount:
        rows = self._rows()
        if not rows:
            raise RuntimeError("iCloud Privacy mailbox missing email alias.")
        email = rows[0]
        return MailboxAccount(email=email, account_id=email, extra=self._extra())

    def account_for_email(self, email: str) -> MailboxAccount:
        target = str(email or "").strip().lower()
        if target:
            return MailboxAccount(email=target, account_id=target, extra=self._extra())
        return self.create_account()

    def _extra(self) -> dict[str, Any]:
        return {
            "provider_name": "icloud_privacy",
            "code_url": self.code_url,
            "mail_url": self.mail_url,
            "inbox_url": self.inbox_url,
        }

    def _has_api_endpoint(self) -> bool:
        return bool(self.code_url or self.mail_url or self.inbox_url)

    def _fetch_json_url(self, url: str, label: str) -> dict[str, Any]:
        response = self.session.get(url, params={"_": int(time.time())}, proxies=self.proxies, timeout=15)
        if response.status_code >= 400:
            raise RuntimeError(f"{label} API request failed: status={response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{label} API did not return JSON: {response.text[:180]}") from exc
        return data if isinstance(data, dict) else {"data": data}

    @staticmethod
    def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else payload

    @staticmethod
    def _message_marker(data: dict[str, Any]) -> str:
        raw_id = str(data.get("message_id") or data.get("id") or data.get("emailId") or "").strip()
        if raw_id:
            return f"mail-api:{raw_id}"
        raw = str(data.get("received_at") or data.get("updated_at") or data.get("subject") or data.get("code") or "")
        return "mail-api:" + str(abs(hash(raw)))

    def _extract_code_from_payload(self, payload: dict[str, Any], code_pattern: str | None = None) -> tuple[str, str]:
        data = self._payload_data(payload)
        if data.get("success") is False or data.get("found") is False:
            return "", self._message_marker(data)
        raw_code = str(data.get("code") or data.get("verification_code") or data.get("latest_verification_code") or "").strip()
        source = raw_code or " ".join(str(data.get(field) or "") for field in ("subject", "text", "content", "html", "body", "message"))
        if code_pattern:
            match = re.search(code_pattern, source)
            code = match.group(1) if match and match.groups() else (match.group(0) if match else "")
        else:
            code = raw_code or extract_verification_code(source, expected_lengths=(6,))
        return code, self._message_marker(data)

    def get_current_ids(self, account: MailboxAccount) -> set[str]:
        if not self._has_api_endpoint():
            return super().get_current_ids(account)
        markers: set[str] = set()
        for url, label in ((self.code_url, "code"), (self.mail_url, "mail")):
            if not url:
                continue
            try:
                _code, marker = self._extract_code_from_payload(self._fetch_json_url(url, label))
                if marker:
                    markers.add(marker)
            except Exception:
                pass
        return markers

    def wait_for_code(
        self,
        account: MailboxAccount,
        *,
        timeout: int = 180,
        before_ids: set[str] | None = None,
        code_pattern: str | None = None,
    ) -> str:
        if not self._has_api_endpoint():
            return super().wait_for_code(account, timeout=timeout, before_ids=before_ids, code_pattern=code_pattern)
        seen = set(before_ids or set())
        deadline = time.time() + int(timeout or 180)
        last_error = ""
        while time.time() < deadline:
            for url, label in ((self.code_url, "code"), (self.mail_url, "mail")):
                if not url:
                    continue
                try:
                    code, marker = self._extract_code_from_payload(self._fetch_json_url(url, label), code_pattern=code_pattern)
                    if marker and marker not in seen and code:
                        return code
                    if marker:
                        seen.add(marker)
                    elif code:
                        return code
                except Exception as exc:
                    last_error = str(exc).splitlines()[0][:160]
            time.sleep(self.poll_interval)
        detail = f": {last_error}" if last_error else ""
        raise TimeoutError(f"Timed out waiting for iCloud Privacy mailbox OTP after {timeout}s{detail}")


class CFWorkerMailbox:
    """Cloudflare Worker / Cloud Mail mailbox adapter."""

    def __init__(self, api_url: str, admin_token: str = "", domain: str = "", fingerprint: str = "", proxy: str | None = None):
        self.api = str(api_url or "").rstrip("/")
        self.admin_token = str(admin_token or "")
        self.domain = str(domain or "").strip().lstrip("@")
        self.fingerprint = str(fingerprint or "")
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self._api_mode = "auto"

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CFWorkerMailbox":
        return cls(
            api_url=str(config.get("cfworker_api_url") or config.get("mailbox_api_url") or ""),
            admin_token=str(config.get("cfworker_admin_token") or config.get("mailbox_admin_token") or ""),
            domain=str(config.get("cfworker_domain") or config.get("mailbox_domain") or ""),
            fingerprint=str(config.get("cfworker_fingerprint") or ""),
            proxy=str(config.get("mailbox_proxy") or config.get("proxy") or "") or None,
        )

    def create_account(self) -> MailboxAccount:
        if not self.api:
            raise RuntimeError("CFWorker/Cloud Mail mailbox missing api_url.")
        local = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        if self._detect_api_mode() == "cloud_mail":
            return self._create_cloud_mail_account(local)
        return self._create_cfworker_account(local)

    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json, text/plain, */*", "content-type": "application/json", "x-admin-auth": self.admin_token}
        if self.fingerprint:
            headers["x-fingerprint"] = self.fingerprint
        return headers

    def _cloud_headers(self) -> dict[str, str]:
        return {"accept": "application/json, text/plain, */*", "content-type": "application/json", "Authorization": self.admin_token}

    @staticmethod
    def _json(response: requests.Response, label: str) -> dict[str, Any]:
        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"{label} did not return JSON: status={response.status_code} body={response.text[:200]}") from exc
        return data if isinstance(data, dict) else {"data": data}

    def _detect_api_mode(self) -> str:
        if self._api_mode in {"cfworker", "cloud_mail"}:
            return self._api_mode
        try:
            response = requests.get(
                f"{self.api}/api/setting/websiteConfig",
                headers={"accept": "application/json, text/plain, */*"},
                proxies=self.proxies,
                timeout=5,
            )
            data = self._json(response, "Cloud Mail websiteConfig")
            config = data.get("data") if isinstance(data, dict) else None
            if data.get("code") == 200 and isinstance(config, dict):
                domains = {str(item or "").strip().lstrip("@") for item in (config.get("domainList") or []) if str(item or "").strip()}
                if self.domain and domains and self.domain not in domains:
                    raise RuntimeError(f"Cloud Mail domain is not enabled: {self.domain}")
                self._api_mode = "cloud_mail"
                return self._api_mode
        except Exception:
            pass
        self._api_mode = "cfworker"
        return self._api_mode

    def _create_cloud_mail_account(self, local: str) -> MailboxAccount:
        if not self.domain:
            raise RuntimeError("Cloud Mail mailbox missing domain.")
        if not self.admin_token:
            raise RuntimeError("Cloud Mail mailbox missing API token.")
        email = f"{local}@{self.domain}"
        response = requests.post(
            f"{self.api}/api/public/addUser",
            json={"list": [{"email": email}]},
            headers=self._cloud_headers(),
            proxies=self.proxies,
            timeout=15,
        )
        data = self._json(response, "Cloud Mail addUser")
        if response.status_code >= 400 or data.get("code") not in (None, 200):
            raise RuntimeError(f"Cloud Mail addUser failed: status={response.status_code} resp={str(data)[:200]}")
        self._api_mode = "cloud_mail"
        return MailboxAccount(email=email, account_id=email, extra={"provider_name": "cloud_mail", "api_url": self.api, "domain": self.domain})

    def _create_cfworker_account(self, local: str) -> MailboxAccount:
        payload: dict[str, Any] = {"enablePrefix": True, "name": local}
        if self.domain:
            payload["domain"] = self.domain
        response = requests.post(f"{self.api}/admin/new_address", json=payload, headers=self._headers(), proxies=self.proxies, timeout=15)
        data = self._json(response, "CFWorker new_address")
        email = str(data.get("email") or data.get("address") or "")
        token = str(data.get("token") or data.get("jwt") or "")
        if not email:
            raise RuntimeError(f"CFWorker new_address did not return email: {str(data)[:200]}")
        self._api_mode = "cfworker"
        return MailboxAccount(email=email, account_id=token or email, extra={"provider_name": "cfworker", "api_url": self.api, "domain": self.domain, "token": token})

    def get_current_ids(self, account: MailboxAccount) -> set[str]:
        try:
            return {str(mail.get("id") or "") for mail in self._get_mails(account) if str(mail.get("id") or "")}
        except Exception:
            return set()

    def wait_for_code(
        self,
        account: MailboxAccount,
        *,
        timeout: int = 180,
        before_ids: set[str] | None = None,
        code_pattern: str | None = None,
    ) -> str:
        seen = set(before_ids or set())
        deadline = time.time() + int(timeout or 180)
        while time.time() < deadline:
            for mail in self._get_mails(account):
                message_id = str(mail.get("id") or "")
                if message_id and message_id in seen:
                    continue
                if message_id:
                    seen.add(message_id)
                raw = str(mail.get("raw") or mail.get("content") or mail.get("text") or mail.get("subject") or "")
                raw = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", " ", raw)
                raw = re.sub(r"m=\+\d+\.\d+|\bt=\d+\b", " ", raw)
                if code_pattern:
                    match = re.search(code_pattern, raw)
                    if match:
                        return match.group(1) if match.groups() else match.group(0)
                code = extract_verification_code(raw, expected_lengths=(6,))
                if code:
                    return code
            time.sleep(3)
        raise TimeoutError(f"Timed out waiting for mailbox OTP after {timeout}s")

    def _get_mails(self, account: MailboxAccount) -> list[dict[str, Any]]:
        if self._detect_api_mode() == "cloud_mail":
            return self._get_cloud_mail_mails(account.email)
        try:
            return self._get_cfworker_mails(account)
        except Exception:
            return self._get_cloud_mail_mails(account.email)

    def _get_cloud_mail_mails(self, email: str) -> list[dict[str, Any]]:
        response = requests.post(
            f"{self.api}/api/public/emailList",
            json={"toEmail": email, "timeSort": "desc", "num": 1, "size": 50},
            headers=self._cloud_headers(),
            proxies=self.proxies,
            timeout=10,
        )
        data = self._json(response, "Cloud Mail emailList")
        if response.status_code >= 400 or data.get("code") not in (None, 200):
            raise RuntimeError(f"Cloud Mail emailList failed: status={response.status_code} resp={str(data)[:200]}")
        items = data.get("data", data)
        if isinstance(items, dict):
            items = items.get("list", [])
        mails: list[dict[str, Any]] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw = " ".join(str(item.get(field) or "") for field in ("subject", "sendEmail", "sendName", "content", "text"))
                mails.append({**item, "id": item.get("emailId", item.get("id", "")), "raw": raw})
        return mails

    def _get_cfworker_mails(self, account: MailboxAccount) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.api}/admin/mails",
            params={"limit": 20, "offset": 0, "address": account.email},
            headers=self._headers(),
            proxies=self.proxies,
            timeout=10,
        )
        data = self._json(response, "CFWorker mails")
        items = data.get("results", data)
        return items if isinstance(items, list) else []
