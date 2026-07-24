"""Proxy utilities — pure functions with no ExtractionContext dependency.

These functions only depend on stdlib (re, hashlib, urllib.parse) and the
shared config constants.  They can be imported freely by context.py without
creating circular dependencies.

Functions that need access to ExtractionContext (e.g. log, redact, state I/O)
live in the ExtractionContext class itself and call these pure utilities.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .config import COUNTRY_CURRENCY, country_selector_re


# ── country utilities ──────────────────────────────────────────────────────

def normalize_country(country: str) -> str:
    """Normalise a country code to uppercase.  Falls back to 'IN' if unknown."""
    value = str(country or "").strip().upper()
    return value if value in COUNTRY_CURRENCY else "IN"


def currency_for_country(country: str) -> str:
    return COUNTRY_CURRENCY.get(normalize_country(country), "INR")


# ── proxy URL normalisation ────────────────────────────────────────────────

def normalize_proxy_url(proxy: str, default_scheme: str = "http") -> str:
    """Normalise a proxy URL: add scheme if missing, percent-encode auth."""
    proxy = str(proxy or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = f"{default_scheme}://{proxy}"

    parsed = urlsplit(proxy)
    if parsed.username is None and parsed.password is None:
        return proxy

    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    username = quote(unquote(parsed.username or ""), safe="-._~")
    auth = username
    if parsed.password is not None:
        auth = f"{auth}:{quote(unquote(parsed.password), safe='-._~')}"
    return urlunsplit((parsed.scheme, f"{auth}@{host}", parsed.path, parsed.query, parsed.fragment))


def normalize_pre_proxy_url(proxy: str) -> str:
    """Normalise a pre-proxy URL: default scheme is socks5h."""
    proxy = str(proxy or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = f"socks5h://{proxy}"
    return normalize_proxy_url(proxy)


# ── proxy identity hashes ──────────────────────────────────────────────────

def proxy_key(proxy: str) -> str:
    """Deterministic hash of a normalised proxy URL."""
    proxy = normalize_proxy_url(proxy)
    return hashlib.sha256(proxy.encode()).hexdigest() if proxy else ""


def proxy_chain_key(proxy: str) -> str:
    """Redacted identity that stays stable across country rewrites.

    The country/region selector in the proxy auth is replaced with a wildcard
    before hashing, so that derived proxies for different countries still share
    the same chain key.
    """
    proxy = unquote(normalize_proxy_url(proxy))
    normalized = country_selector_re().sub(
        lambda match: f"{match.group('name')}{match.group('separator')}*",
        proxy,
    )
    return hashlib.sha256(normalized.encode()).hexdigest()[:10] if normalized else ""


def proxy_short(proxy: str) -> str:
    """Human-readable short proxy label for log output."""
    proxy = normalize_proxy_url(proxy)
    if not proxy:
        return "direct"
    digest = hashlib.sha256(proxy.encode()).hexdigest()[:10]
    return f"proxy#{digest}"


def proxy_label(proxy: str) -> str:
    """Alias for proxy_short — used in log redaction and display."""
    return proxy_short(proxy)


# ── proxy chain derivation ─────────────────────────────────────────────────

def proxy_for_country(proxy: str, country: str) -> str:
    """Rewrite only a proxy auth country selector while retaining its sticky session."""
    proxy = normalize_proxy_url(proxy)
    target_country = normalize_country(country).lower()
    if not proxy:
        raise RuntimeError("代理为空，无法派生地区链路")

    parsed = urlsplit(proxy)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    sel_re = country_selector_re()
    replacements: int = 0

    def replace_country(match: re.Match[str]) -> str:
        nonlocal replacements
        replacements += 1
        current = match.group("value")
        value = target_country.upper() if current.isupper() else target_country
        return f"{match.group('name')}{match.group('separator')}{value}"

    username = sel_re.sub(replace_country, username)
    password = sel_re.sub(replace_country, password)
    if not replacements:
        raise RuntimeError(
            f"代理未包含可改写的 country/region 选择器: {proxy_label(proxy)}"
        )

    hostname = parsed.hostname or ""
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    auth = quote(username, safe="-._~")
    if parsed.password is not None:
        auth = f"{auth}:{quote(password, safe='-._~')}"
    derived = urlunsplit((parsed.scheme, f"{auth}@{host}", parsed.path, parsed.query, parsed.fragment))
    return derived


def proxy_state_key(group: str, proxy: str) -> str:
    """Key used in proxy_state.json for a given group and proxy."""
    if group == "seed":
        return proxy_chain_key(proxy)
    return proxy_key(proxy)


def proxy_pair_key(checkout_proxy: str, provider_proxy: str) -> str:
    checkout_key = proxy_key(checkout_proxy)
    provider_key = proxy_key(provider_proxy)
    return f"{checkout_key}:{provider_key}" if checkout_key and provider_key else ""


# ── error classification ───────────────────────────────────────────────────

def is_direct_remove_proxy_error(reason: str) -> bool:
    """Return True if the error indicates a permanently broken proxy."""
    text = str(reason or "").lower()
    return any(
        marker in text
        for marker in (
            "proxy authentication",
            "proxy auth",
            "resolve proxy",
            "could not resolve proxy",
            "invalid proxy",
            "malformed proxy",
            "unsupported proxy",
            "http 407",
            "status 407",
        )
    )


def is_proxy_health_failure(reason: str) -> bool:
    """Return True if the error indicates a transient proxy health issue."""
    text = str(reason or "").lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "connection refused",
            "connection reset",
            "eof",
            "broken pipe",
            "no route to host",
            "network is unreachable",
            "tunnel connection failed",
            "proxy error",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "too many requests",
            "status 502",
            "status 503",
            "status 504",
            "status 429",
        )
    )


# ── UPI unavailable error detection ───────────────────────────────────────

UPI_UNAVAILABLE_ERROR: str = "当前账号支付方式不支持 UPI"


def is_upi_unavailable_error(value: Any) -> bool:
    """Check if an error indicates the account does not support UPI payments.

    When this returns True, proxy failures should NOT be recorded against
    the proxy because the failure is account-level, not proxy-level.
    """
    text = str(value or "")
    return UPI_UNAVAILABLE_ERROR in text or "当前 checkout 不支持 UPI" in text


# ── session helpers ──────────────────────────────────────────────────────

def set_proxy(session: Any, proxy: str, scheme: str = "http") -> None:
    """Set proxy on a requests / curl_cffi session.

    This is a pure function — it does NOT register the proxy for redaction.
    Callers are responsible for redaction via ExtractionContext if needed.
    """
    proxy = normalize_proxy_url(proxy, scheme)
    if hasattr(session, "trust_env"):
        session.trust_env = False
    session.proxies = {"http": proxy, "https": proxy} if proxy else {}
