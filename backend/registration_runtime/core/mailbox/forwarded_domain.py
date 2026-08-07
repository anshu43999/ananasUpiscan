from __future__ import annotations

import random
import re
import string
import time
from dataclasses import dataclass, field
from typing import Any

from core.base_sms import extract_verification_code


@dataclass
class MailboxAccount:
    email: str
    account_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ForwardedDomainMailbox:
    """Catch-all domain mailbox that reads forwarded OTP mail through IMAP."""

    _IMAP_HOSTS = {
        "163.com": "imap.163.com",
        "126.com": "imap.126.com",
        "yeah.net": "imap.yeah.net",
        "qq.com": "imap.qq.com",
        "gmail.com": "imap.gmail.com",
        "outlook.com": "imap-mail.outlook.com",
        "hotmail.com": "imap-mail.outlook.com",
    }

    def __init__(
        self,
        domain: str,
        imap_user: str = "",
        imap_pass: str = "",
        imap_host: str = "",
        imap_port: int = 993,
    ):
        self.domain = str(domain or "").strip().lstrip("@")
        self.imap_user = str(imap_user or "").strip()
        self.imap_pass = str(imap_pass or "")
        self.imap_host = str(imap_host or "").strip()
        self.imap_port = int(imap_port or 993)
        if not self.imap_host and "@" in self.imap_user:
            user_domain = self.imap_user.rsplit("@", 1)[1].lower()
            self.imap_host = self._IMAP_HOSTS.get(user_domain, f"imap.{user_domain}")

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ForwardedDomainMailbox":
        return cls(
            domain=str(config.get("mailbox_domain") or config.get("forward_mailbox_domain") or config.get("cfworker_domain") or ""),
            imap_user=str(config.get("mailbox_imap_user") or config.get("forward_imap_user") or config.get("ddg_imap_user") or ""),
            imap_pass=str(config.get("mailbox_imap_pass") or config.get("forward_imap_pass") or config.get("ddg_imap_pass") or ""),
            imap_host=str(config.get("mailbox_imap_host") or config.get("forward_imap_host") or config.get("ddg_imap_host") or ""),
            imap_port=int(config.get("mailbox_imap_port") or config.get("forward_imap_port") or 993),
        )

    def create_account(self) -> MailboxAccount:
        if not self.domain:
            raise RuntimeError("Forwarded domain mailbox missing mailbox_domain.")
        local = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        email = f"{local}@{self.domain}"
        return MailboxAccount(email=email, account_id=email, extra={"provider_name": "forwarded_domain", "domain": self.domain})

    def account_for_email(self, email: str) -> MailboxAccount:
        target = str(email or "").strip().lower()
        if not target or "@" not in target:
            return self.create_account()
        return MailboxAccount(email=target, account_id=target, extra={"provider_name": "forwarded_domain", "domain": self.domain})

    def get_current_ids(self, account: MailboxAccount) -> set[str]:
        if not self.imap_user or not self.imap_pass:
            return set()
        try:
            return self._imap_ids()
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
        if not self.imap_user or not self.imap_pass:
            raise RuntimeError("Forwarded domain mailbox missing IMAP user/password.")
        seen = set(before_ids or set())
        deadline = time.time() + int(timeout or 180)
        last_error = ""
        while time.time() < deadline:
            try:
                messages = self._imap_messages(limit=40, account=account)
                last_error = ""
            except (TimeoutError, OSError, ConnectionError) as exc:
                last_error = str(exc) or exc.__class__.__name__
                time.sleep(5)
                continue
            for message_id, raw in messages:
                if message_id in seen:
                    continue
                seen.add(message_id)
                if not self._message_matches_account(raw, account):
                    continue
                text = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
                text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", " ", text)
                if code_pattern:
                    match = re.search(code_pattern, text)
                    if match:
                        return match.group(1) if match.groups() else match.group(0)
                code = extract_verification_code(text, expected_lengths=(6,))
                if code:
                    return code
            time.sleep(5)
        detail = f"; last_imap_error={last_error}" if last_error else ""
        raise TimeoutError(f"Timed out waiting for forwarded mailbox OTP after {timeout}s: {account.email}{detail}")

    def _message_matches_account(self, raw: str, account: MailboxAccount) -> bool:
        lowered = str(raw or "").lower()
        target = str(account.email or "").strip().lower()
        if not target or target not in lowered:
            return False
        return "openai" in lowered or "chatgpt" in lowered

    def _connect_imap(self):
        import imaplib

        if not self.imap_host:
            raise RuntimeError("Forwarded domain mailbox missing IMAP host.")
        conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=10)
        if any(host in self.imap_host for host in ("163.com", "126.com", "yeah.net")):
            imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")
            conn._simple_command("ID", '("name" "IMAPClient" "version" "1.0")')
        conn.login(self.imap_user, self.imap_pass)
        conn.select("INBOX", readonly=True)
        return conn

    def _imap_ids(self) -> set[str]:
        conn = None
        try:
            conn = self._connect_imap()
            _status, msg_nums = conn.search(None, "ALL")
            ids = msg_nums[0].split() if msg_nums and msg_nums[0] else []
            return {item.decode("ascii", errors="ignore") for item in ids}
        finally:
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass

    def _imap_messages(self, *, limit: int, account: MailboxAccount | None = None) -> list[tuple[str, str]]:
        import email as email_lib

        conn = None
        messages: list[tuple[str, str]] = []
        try:
            conn = self._connect_imap()
            target = str(account.email or "").strip().lower() if account else ""
            ids: list[bytes] = []
            if target:
                for criteria in (
                    ("TO", f'"{target}"'),
                    ("HEADER", "Delivered-To", f'"{target}"'),
                    ("HEADER", "X-Original-To", f'"{target}"'),
                    ("HEADER", "Envelope-To", f'"{target}"'),
                ):
                    try:
                        _status, found = conn.search(None, *criteria)
                    except Exception:
                        continue
                    ids.extend(found[0].split() if found and found[0] else [])
            if not ids:
                _status, msg_nums = conn.search(None, "ALL")
                ids = msg_nums[0].split() if msg_nums and msg_nums[0] else []
            ids = list(dict.fromkeys(ids))
            for mid in reversed(ids[-limit:]):
                mid_text = mid.decode("ascii", errors="ignore")
                try:
                    _status, msg_data = conn.fetch(mid, "(RFC822)")
                    raw_bytes = msg_data[0][1] if msg_data and msg_data[0] else b""
                except (TimeoutError, OSError, ConnectionError):
                    continue
                msg = email_lib.message_from_bytes(raw_bytes)
                parts = [str(msg.get(name, "") or "") for name in ("To", "Delivered-To", "X-Original-To", "Envelope-To", "From", "Subject")]
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() not in ("text/plain", "text/html"):
                            continue
                        payload = part.get_payload(decode=True)
                        if payload:
                            parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        parts.append(payload.decode(msg.get_content_charset() or "utf-8", errors="replace"))
                messages.append((mid_text, " ".join(parts)))
        finally:
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass
        return messages

